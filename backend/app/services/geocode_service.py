from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Tuple

from ..domain.geocode import GeocodeCandidate, GeocodeResult, GeocodeStatus
from ..domain.models import Coordinate
from ..providers.geocoding.base import Geocoder
from .address_normalizer import NormalizedAddress, matches_city, normalize

# Provider calls allowed per address: full query, query without the unit,
# then the structured street/city form.
MAX_ATTEMPTS_PER_ADDRESS = 3

# Exact wording required for a street-level match.
APPROXIMATE_MATCH_MESSAGE = "Street found, house not found. The pin is approximate."


@dataclass
class BatchGeocodeRequestItem:
    index: int
    original: str
    trimmed: str


@dataclass
class BatchGeocodeResponseItem:
    index: int
    original: str
    status: str
    coordinate: Dict[str, float] | None = None
    display_name: str | None = None
    candidates: List[Dict[str, Any]] | None = None
    error_message: str | None = None
    message: str | None = None
    error_kind: str | None = None
    searched_as: str | None = None
    house: str | None = None
    unit: str | None = None
    unit_kind: str | None = None
    unit_inferred: bool = False
    dropped_candidates: int = 0
    scope_message: str | None = None


@dataclass(frozen=True)
class _Attempt:
    """One provider call in the fallback chain."""

    text: Optional[str]
    structured_street: Optional[str] = None
    city: Optional[str] = None

    @property
    def searched_as(self) -> Optional[str]:
        """What was asked for. Structured attempts also send the city."""
        if self.text:
            return self.text
        if self.structured_street:
            if self.city:
                return f"{self.structured_street}, {self.city}"
            return self.structured_street
        return None

    def request_key(self) -> Optional[Tuple[str, ...]]:
        """Identity of the underlying provider request, for de-duplication.

        A structured request is not the same as a free-text one even when the
        strings match: it also carries the city, which scopes the search.
        """
        if self.text:
            return ("text", self.text.strip().lower())
        if self.structured_street and self.city:
            return (
                "structured",
                self.structured_street.strip().lower(),
                self.city.strip().lower(),
            )
        return None


def _has_house_number(address: Any) -> bool:
    if not isinstance(address, dict):
        return False
    value = address.get("house_number")
    return bool(isinstance(value, (str, int)) and str(value).strip())


def _street_key(candidate: GeocodeCandidate) -> str:
    """Group key so one street yields one representative candidate."""
    address = candidate.address if isinstance(candidate.address, dict) else {}
    for field in ("road", "pedestrian", "footway", "residential"):
        value = address.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return candidate.display_name.strip().lower()


def _importance(candidate: GeocodeCandidate) -> float:
    return candidate.importance if isinstance(candidate.importance, (int, float)) else -1.0


def _representative(candidates: List[GeocodeCandidate]) -> GeocodeCandidate:
    """Pick one candidate to stand for the group, preferring the most important."""
    return max(candidates, key=_importance)


def _address_key(candidate: GeocodeCandidate) -> Optional[Tuple[str, str]]:
    """House number plus street, or None when the row has no house number."""
    address = candidate.address if isinstance(candidate.address, dict) else {}
    house = address.get("house_number")
    if not isinstance(house, str) or not house.strip():
        return None
    return (house.strip().lower(), _street_key(candidate))


def _collapse_same_house(candidates: List[GeocodeCandidate]) -> List[GeocodeCandidate]:
    """Drop rows that describe the same house.

    Nominatim lists an address and any place sitting at that address as
    separate rows - "8, Покровська вулиця" next to "Ліцей №100 «Поділ», 8,
    Покровська вулиця". Same house number on the same street is one location,
    so presenting it as a choice would be misleading.
    """
    best_index: Dict[Tuple[str, str], int] = {}
    replaced: Set[int] = set()
    for index, candidate in enumerate(candidates):
        key = _address_key(candidate)
        if key is None:
            continue
        current = best_index.get(key)
        if current is None:
            best_index[key] = index
        elif _importance(candidate) > _importance(candidates[current]):
            replaced.add(current)
            best_index[key] = index
        else:
            replaced.add(index)
    return [c for i, c in enumerate(candidates) if i not in replaced]


def _scope_message(dropped: int, city: Optional[str]) -> str:
    noun = "match" if dropped == 1 else "matches"
    verb = "was" if dropped == 1 else "were"
    where = f"outside {city}" if city else "outside the chosen city"
    return f"{dropped} {noun} {verb} ignored ({where})."


# How much a result tells us, used to keep the best answer across attempts.
_SPECIFICITY = {
    GeocodeStatus.RESOLVED: 3,
    GeocodeStatus.AMBIGUOUS: 3,
    GeocodeStatus.PARTIAL: 2,
    GeocodeStatus.NOT_FOUND: 1,
}


class GeocodeService:
    def __init__(self, geocoder: Geocoder) -> None:
        self._geocoder = geocoder
        self._batch_cache: Dict[str, GeocodeResult] = {}

    # -- attempts -----------------------------------------------------------

    def _attempts(
        self, normalized: NormalizedAddress, city: Optional[str] = None
    ) -> List[_Attempt]:
        """Build the fallback chain, dropping attempts that repeat an earlier one."""
        attempts: List[_Attempt] = []

        def add(attempt: _Attempt) -> None:
            key = attempt.request_key()
            if key is None or key in {existing.request_key() for existing in attempts}:
                return
            attempts.append(attempt)

        add(_Attempt(text=normalized.query))
        if normalized.query_without_unit != normalized.query:
            add(_Attempt(text=normalized.query_without_unit))
        # The structured lookup is scoped to a city, so it needs one: either the
        # city detected inside the address or the city the user picked.
        structured_city = normalized.city or city
        if normalized.structured_street and structured_city:
            add(
                _Attempt(
                    text=None,
                    structured_street=normalized.structured_street,
                    city=structured_city,
                )
            )
        return attempts[:MAX_ATTEMPTS_PER_ADDRESS]

    async def _run_attempt(self, attempt: _Attempt, city: Optional[str]) -> GeocodeResult:
        if attempt.structured_street and city:
            return await self._geocoder.geocode_structured(attempt.structured_street, city)
        if attempt.text is not None:
            return await self._geocoder.geocode(attempt.text)
        return GeocodeResult(status=GeocodeStatus.NOT_FOUND)

    # -- classification -----------------------------------------------------

    def _classify(
        self,
        raw: GeocodeResult,
        normalized: NormalizedAddress,
        city: Optional[str],
        searched_as: Optional[str],
    ) -> GeocodeResult:
        """Turn a provider result into the status the user sees.

        City scoping and the house-level/partial split live here, in the
        service layer, so they apply to every provider.
        """
        common = {
            "searched_as": searched_as,
            "house": normalized.house,
            "unit": normalized.unit,
            "unit_kind": normalized.unit_kind,
            "unit_inferred": normalized.unit_inferred,
        }

        if raw.status == GeocodeStatus.ERROR:
            return replace(raw, **common)

        candidates = list(raw.candidates)
        dropped = 0
        if city:
            kept = []
            for candidate in candidates:
                if matches_city(candidate.address, city):
                    kept.append(candidate)
                else:
                    dropped += 1
            candidates = kept

        scope_message = _scope_message(dropped, city) if dropped else None

        if not candidates:
            # Nothing in scope: fall back to the provider's own verdict, but
            # tell the user that matches existed elsewhere.
            if raw.candidates and dropped:
                return GeocodeResult(
                    status=GeocodeStatus.NOT_FOUND,
                    error_message="No results in the selected city",
                    dropped_candidates=dropped,
                    scope_message=scope_message,
                    **common,
                )
            return replace(raw, **common, dropped_candidates=dropped)

        house_level = [
            c for c in candidates if _has_house_number(c.address)
        ]
        house_level = _collapse_same_house(house_level)

        if len(house_level) >= 2:
            return GeocodeResult(
                status=GeocodeStatus.AMBIGUOUS,
                candidates=house_level,
                error_message="Multiple house-level candidates found",
                dropped_candidates=dropped,
                scope_message=scope_message,
                **common,
            )

        if len(house_level) == 1:
            best = house_level[0]
            return GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                coordinate=Coordinate(best.latitude, best.longitude),
                display_name=best.display_name,
                dropped_candidates=dropped,
                scope_message=scope_message,
                **common,
            )

        # Nothing reached house level. Several hits usually describe the same
        # street, so collapse them into one approximate representative instead
        # of asking the user to choose between duplicates.
        representative = _representative(candidates)
        return GeocodeResult(
            status=GeocodeStatus.PARTIAL,
            coordinate=Coordinate(representative.latitude, representative.longitude),
            display_name=representative.display_name,
            candidates=[representative],
            error_message="Found street but not a specific house",
            message=APPROXIMATE_MATCH_MESSAGE,
            dropped_candidates=dropped,
            scope_message=scope_message,
            **common,
        )

    # -- single address -----------------------------------------------------

    async def geocode_one(self, original: str, trimmed: str, city: Optional[str]) -> GeocodeResult:
        normalized = normalize(trimmed)
        attempts = self._attempts(normalized, city)
        if not attempts:
            return GeocodeResult(status=GeocodeStatus.NOT_FOUND, searched_as=normalized.query)

        # Cache key includes the city: the same line can resolve differently
        # when the user changes scope.
        cache_key = f"{(normalized.query or '').strip().lower()}|{(city or '').strip().lower()}"
        cached = self._batch_cache.get(cache_key)
        if cached is not None and cached.status != GeocodeStatus.ERROR:
            return cached

        last: Optional[GeocodeResult] = None
        for attempt in attempts:
            raw = await self._run_attempt(attempt, normalized.city or city)
            searched_as = attempt.searched_as
            if raw.status == GeocodeStatus.ERROR:
                # A rate-limit or setup failure will not improve by trying the
                # other forms, and burning attempts makes it worse.
                return replace(raw, searched_as=searched_as)

            result = self._classify(raw, normalized, city, searched_as)
            # Keep the most informative answer seen so far. Ties keep the earlier
            # attempt, which is the one most likely to carry a drop notice.
            if last is None or _SPECIFICITY[result.status] > _SPECIFICITY[last.status]:
                last = result

            if result.status in (GeocodeStatus.RESOLVED, GeocodeStatus.AMBIGUOUS):
                # Reached house level: nothing left to improve on.
                self._batch_cache[cache_key] = result
                return result
            if result.status == GeocodeStatus.PARTIAL and not normalized.house:
                # The input asked for a street only, so this is the best answer
                # the remaining attempts could give.
                self._batch_cache[cache_key] = result
                return result
            # Not found, or partial while an explicit house number is known:
            # the next form may resolve it.

        # Nothing resolved to house level. Errors are never cached, so a retry
        # after a rate limit can still succeed.
        result = last or GeocodeResult(status=GeocodeStatus.NOT_FOUND, searched_as=normalized.query)
        if result.status != GeocodeStatus.ERROR:
            self._batch_cache[cache_key] = result
        return result

    # -- batch --------------------------------------------------------------

    async def geocode_batch(
        self, items: List[BatchGeocodeRequestItem], city: Optional[str] = None
    ) -> List[BatchGeocodeResponseItem]:
        if len(items) > 5:
            raise ValueError("Batch size cannot exceed 5")

        # Identical lines (and different casing) are resolved once.
        unique: Dict[str, BatchGeocodeRequestItem] = {}
        order: List[str] = []
        for item in items:
            key = item.trimmed.strip().lower()
            if key not in unique:
                unique[key] = item
                order.append(key)

        resolved: Dict[str, GeocodeResult] = {}
        for key in order:
            item = unique[key]
            resolved[key] = await self.geocode_one(item.original, item.trimmed, city)

        results: List[BatchGeocodeResponseItem] = []
        for item in items:
            res = resolved[item.trimmed.strip().lower()]
            results.append(self._to_response_item(item, res))
        return results

    def _to_response_item(
        self, item: BatchGeocodeRequestItem, res: GeocodeResult
    ) -> BatchGeocodeResponseItem:
        coordinate = None
        if res.coordinate is not None:
            coordinate = {"latitude": res.coordinate.latitude, "longitude": res.coordinate.longitude}
        candidates = None
        if res.candidates:
            candidates = [
                {
                    "display_name": c.display_name,
                    "latitude": c.latitude,
                    "longitude": c.longitude,
                    "address": c.address,
                }
                for c in res.candidates
            ]
        return BatchGeocodeResponseItem(
            index=item.index,
            original=item.original,
            status=res.status.value,
            coordinate=coordinate,
            display_name=res.display_name,
            candidates=candidates,
            error_message=res.error_message,
            message=res.message,
            error_kind=res.error_kind.value if res.error_kind else None,
            searched_as=res.searched_as,
            house=res.house,
            unit=res.unit,
            unit_kind=res.unit_kind,
            unit_inferred=res.unit_inferred,
            dropped_candidates=res.dropped_candidates,
            scope_message=res.scope_message,
        )