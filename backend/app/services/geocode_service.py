from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Tuple

from ..domain.geocode import GeocodeCandidate, GeocodeResult, GeocodeStatus
from ..domain.models import Coordinate
from ..providers.geocoding.base import Geocoder
from .address_normalizer import (
    NormalizedAddress,
    _fold,
    city_profile_for,
    detect_city,
    house_in_text,
    house_numbers_equal,
    locality_names,
    matches_city,
    normalize,
)
from .list_checks import (
    DEFAULT_OUTLIER_KM,
    CitySuggestion,
    describe_outlier,
    flag_distance_outliers,
    infer_dominant_city,
)

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
    found_house: str | None = None
    found_city: str | None = None
    needs_check: bool = False
    needs_check_reason: str | None = None
    retry_city: str | None = None


@dataclass
class _TraceCandidate:
    """One provider candidate, as it was reported."""

    display_name: str
    house_number: Optional[str] = None
    city: Optional[str] = None


@dataclass
class _TraceStep:
    """Why one fallback attempt ended the way it did.

    Built for ``measure_quality.py --trace`` when a "resolved" row turns out to
    be wrong: the question is always which request was sent, what came back, and
    which rule accepted or rejected it.
    """

    index: int
    original: str
    attempt_number: int
    kind: str
    query: Optional[str] = None
    structured_street: Optional[str] = None
    request_city: Optional[str] = None
    scope_city: Optional[str] = None
    asked_house: Optional[str] = None
    candidates: List[_TraceCandidate] = field(default_factory=list)
    outcome: Optional[str] = None
    notes: List[str] = field(default_factory=list)
    from_cache: bool = False

    def as_dict(self) -> dict:
        return {
            "address": self.original,
            "attempt": self.attempt_number,
            "kind": self.kind,
            "query": self.query,
            "structured_street": self.structured_street,
            "request_city": self.request_city,
            "scope_city": self.scope_city,
            "asked_house": self.asked_house,
            "candidates": [
                {
                    "display_name": c.display_name,
                    "house_number": c.house_number,
                    "city": c.city,
                }
                for c in self.candidates
            ],
            "outcome": self.outcome,
            "notes": self.notes,
            "from_cache": self.from_cache,
        }


@dataclass(frozen=True)
class _Attempt:
    """One provider call in the fallback chain."""

    text: Optional[str]
    structured_street: Optional[str] = None
    city: Optional[str] = None
    # The house number this attempt actually asks for. It comes from the text
    # being sent, so it can differ from ``NormalizedAddress.house``: attempt 1
    # sends the line as written ("51-53") while attempt 2 drops the unit and
    # sends "51".
    asked_house: Optional[str] = None
    # Street with no house number, for the last-resort lookup.
    street_only: Optional[str] = None

    @property
    def is_street_only(self) -> bool:
        return self.street_only is True

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


def _house_number_of(candidate: GeocodeCandidate) -> Optional[str]:
    address = candidate.address if isinstance(candidate.address, dict) else {}
    value = address.get("house_number")
    if isinstance(value, (str, int)) and str(value).strip():
        return str(value).strip()
    return None


def _has_house_number(address: Any) -> bool:
    if not isinstance(address, dict):
        return False
    value = address.get("house_number")
    return bool(isinstance(value, (str, int)) and str(value).strip())


# User-facing copy when a street is right but the house is not what was asked.
HOUSE_MISMATCH_MESSAGE = "Found a different house number. Please check this match."

# Shown when even the street does not exist in the active city, so the user is
# not offered a retry that provably cannot succeed.
NO_SUCH_ADDRESS_MESSAGE = "No such address found in {city} in the map data"


def house_mismatch_message(requested: Optional[str], found: Optional[str]) -> str:
    """Wording for a match that is on the right street but the wrong house."""
    if requested and found:
        return f"Found {found}, you asked for {requested}."
    return HOUSE_MISMATCH_MESSAGE


def _street_key(candidate: GeocodeCandidate) -> str:
    """Group key so one street yields one representative candidate."""
    address = candidate.address if isinstance(candidate.address, dict) else {}
    for field in ("road", "pedestrian", "footway", "residential"):
        value = address.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return candidate.display_name.strip().lower()


def _found_city(candidate: GeocodeCandidate) -> Optional[str]:
    """The city a candidate sits in, as the provider reports it."""
    localities = locality_names(candidate.address)
    return localities[0] if localities else None


def _same_city(one: str, other: str) -> bool:
    """City names that mean the same place, via the known aliases."""
    profile = city_profile_for(one)
    wanted = {_fold(a) for a in profile.aliases} if profile else {_fold(one)}
    return _fold(other) in wanted


def _detected_city(results: List[BatchGeocodeResponseItem]) -> Optional[str]:
    """The city the input lines named, used when the user picked none.

    Majority over what the rows asked for, so one line mentioning another city
    does not scope the rest of the list.
    """
    counts: Dict[str, int] = {}
    for row in results:
        if not row.searched_as or not row.house:
            continue
        profile = detect_city(row.searched_as)
        if profile is not None:
            counts[profile.display] = counts.get(profile.display, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


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


def _is_final_ambiguity(result: GeocodeResult, asked: Optional[str] = None) -> bool:
    """True when an ambiguous result is the best this address can offer.

    Two matches for the house the user asked for is a real decision for them
    to make. A different house number is not an ambiguity, it is a wrong
    answer, and a further attempt may still find the right one.

    ``asked`` is the number the attempt that produced this result sent, which
    is not always ``result.house``: an attempt that dropped a unit asked for a
    shorter number than the parsed one.
    """
    if result.status != GeocodeStatus.AMBIGUOUS:
        return result.status == GeocodeStatus.RESOLVED
    requested = asked if asked is not None else result.house
    if not requested:
        return True
    return house_numbers_equal(requested, result.found_house)


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
    def __init__(
        self,
        geocoder: Geocoder,
        *,
        max_outlier_km: float = DEFAULT_OUTLIER_KM,
        trace: bool = False,
    ) -> None:
        self._geocoder = geocoder
        self._batch_cache: Dict[str, GeocodeResult] = {}
        self._max_outlier_km = max_outlier_km
        self._city_suggestion: Optional[CitySuggestion] = None
        self._trace_enabled = trace
        self._trace: List[_TraceStep] = []

    # -- trace --------------------------------------------------------------

    @property
    def trace(self) -> List[_TraceStep]:
        """Per-attempt trace of the last batch, in call order."""
        return list(self._trace)

    def trace_as_dicts(self) -> List[dict]:
        return [step.as_dict() for step in self._trace]

    def _new_step(
        self, index: int, original: str, normalized: NormalizedAddress
    ) -> Optional[_TraceStep]:
        if not self._trace_enabled:
            return None
        return _TraceStep(
            index=index,
            original=original,
            attempt_number=0,
            kind="",
            scope_city=None,
            asked_house=normalized.house,
        )

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

        add(_Attempt(text=normalized.query, asked_house=house_in_text(normalized.query)))
        if normalized.query_without_unit != normalized.query:
            add(
                _Attempt(
                    text=normalized.query_without_unit,
                    asked_house=house_in_text(normalized.query_without_unit),
                )
            )
        # The structured lookup is scoped to a city, so it needs one. The city
        # the user picked wins over the one written in the address: they can
        # deliberately re-scope a line that names somewhere else.
        structured_city = city or normalized.city
        if normalized.structured_street and structured_city:
            add(
                _Attempt(
                    text=None,
                    structured_street=normalized.structured_street,
                    city=structured_city,
                    asked_house=house_in_text(normalized.structured_street),
                )
            )
        return attempts[:MAX_ATTEMPTS_PER_ADDRESS]

    def _street_only_attempt(
        self, normalized: NormalizedAddress, city: Optional[str]
    ) -> Optional[_Attempt]:
        """Last resort: the street on its own, inside the active city.

        Only built when there is a street to look up and a city to scope it to.
        Used when every candidate was rejected as out of scope, to tell "this
        city has no such house" from "we cannot find it", instead of offering a
        retry that cannot succeed.
        """
        scope_city = city or normalized.city
        if not normalized.street_only or not scope_city:
            return None
        return _Attempt(
            text=None,
            structured_street=normalized.street_only,
            city=scope_city,
            asked_house=None,
            street_only=normalized.street_only,
        )

    async def _run_attempt(self, attempt: _Attempt, city: Optional[str]) -> GeocodeResult:
        if attempt.structured_street:
            # The attempt already carries the city it should be scoped to.
            scoped = attempt.city or city
            if scoped:
                return await self._geocoder.geocode_structured(attempt.structured_street, scoped)
            return GeocodeResult(status=GeocodeStatus.NOT_FOUND)
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
        notes: Optional[List[str]] = None,
        asked_house: Optional[str] = None,
    ) -> GeocodeResult:
        """Turn a provider result into the status the user sees.

        City scoping and the house-level/partial split live here, in the
        service layer, so they apply to every provider. ``notes`` collects the
        accept/reject reasoning for the trace, so the rule and its explanation
        cannot drift apart.

        ``asked_house`` is the number *this attempt* sent, which is not always
        ``normalized.house``: an attempt that dropped a unit sends a shorter
        number, and judging it against the parsed one would reject its own
        correct answer.
        """

        def note(message: str) -> None:
            if notes is not None:
                notes.append(message)
        common = {
            "searched_as": searched_as,
            "house": normalized.house,
            "unit": normalized.unit,
            "unit_kind": normalized.unit_kind,
            "unit_inferred": normalized.unit_inferred,
        }
        asked = asked_house if asked_house is not None else normalized.house

        if raw.status == GeocodeStatus.ERROR:
            return replace(raw, **common)

        # The city the user picked wins, but a city found inside the address is
        # used too: "вулиця Дмитрівська, 86, Київ" must not resolve to Яготин
        # just because nobody filled the City field in.
        scope_city = city or normalized.city
        if scope_city:
            note(f"scope: {scope_city}")
        else:
            note("scope: none")
        candidates = list(raw.candidates)
        dropped = 0
        if scope_city:
            kept = []
            for candidate in candidates:
                if matches_city(candidate.address, scope_city):
                    kept.append(candidate)
                else:
                    dropped += 1
                    found_city = _found_city(candidate) or "no city"
                    note(
                        f"rejected {candidate.display_name[:60]}: "
                        f"house {_house_number_of(candidate) or '-'} in {found_city}"
                    )
            if not dropped:
                note(f"accepted {len(kept)} candidate(s) inside {scope_city}")
            candidates = kept

        scope_message = _scope_message(dropped, scope_city) if dropped else None

        if not candidates:
            # Nothing in scope. Say where the matches actually were, but keep
            # the status non-terminal: an out-of-scope hit must not stop the
            # chain, because the structured attempt may find the right street.
            if raw.candidates and dropped:
                elsewhere = raw.candidates[0]
                found_city = _found_city(elsewhere)
                where = f"Found in {found_city}" if found_city else "Found outside the active city"
                return GeocodeResult(
                    status=GeocodeStatus.NOT_FOUND,
                    error_message=f"{where}, outside {scope_city}",
                    found_house=_house_number_of(elsewhere),
                    found_city=found_city,
                    needs_check=True,
                    needs_check_reason=f"{where}, outside {scope_city}",
                    retry_city=scope_city,
                    dropped_candidates=dropped,
                    scope_message=scope_message,
                    all_out_of_scope=True,
                    **common,
                )
            return replace(raw, **common, dropped_candidates=dropped)

        house_level = [c for c in candidates if _has_house_number(c.address)]
        house_level = _collapse_same_house(house_level)

        # A house number only counts as "resolved" when it is the one that was
        # asked for. "130/1" is not "1" and "40/5" is not "40": both are a
        # different address on the right street, so they must not look certain.
        if asked:
            matching = [
                c
                for c in house_level
                if house_numbers_equal(asked, _house_number_of(c))
            ]
            mismatched = [c for c in house_level if c not in matching]
        else:
            matching = list(house_level)
            mismatched = []

        if len(matching) >= 2:
            note(f"ambiguous: {len(matching)} candidates for house {asked}")
            return GeocodeResult(
                status=GeocodeStatus.AMBIGUOUS,
                candidates=matching,
                error_message="Multiple house-level candidates found",
                found_house=_house_number_of(matching[0]),
                dropped_candidates=dropped,
                scope_message=scope_message,
                **common,
            )

        if len(matching) == 1:
            best = matching[0]
            note(
                f"accepted {_house_number_of(best) or 'street-level'} "
                f"= requested {asked or '(street only)'}"
            )
            return GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                coordinate=Coordinate(best.latitude, best.longitude),
                display_name=best.display_name,
                found_house=_house_number_of(best),
                found_city=_found_city(best),
                dropped_candidates=dropped,
                scope_message=scope_message,
                **common,
            )

        if mismatched:
            # The street is right, the house is not. This is one wrong address,
            # not a choice between candidates, so exactly one candidate is
            # offered and the user confirms it in one tap. Returning the whole
            # mismatched set would present a list of addresses that are all
            # wrong, which is noise rather than a decision.
            best = _representative(mismatched)
            found = _house_number_of(best)
            note(f"house mismatch: requested {asked}, found {found}")
            return GeocodeResult(
                status=GeocodeStatus.AMBIGUOUS,
                candidates=[best],
                coordinate=Coordinate(best.latitude, best.longitude),
                display_name=best.display_name,
                error_message="Found a different house number",
                message=house_mismatch_message(asked, found),
                found_house=found,
                dropped_candidates=dropped,
                scope_message=scope_message,
                **common,
            )

        # Nothing reached house level. Several hits usually describe the same
        # street, so collapse them into one approximate representative instead
        # of asking the user to choose between duplicates.
        representative = _representative(candidates)
        note(f"partial: street only, no candidate for house {asked or '-'}")
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

    async def geocode_one(
        self, original: str, trimmed: str, city: Optional[str], index: int = 0
    ) -> GeocodeResult:
        normalized = normalize(trimmed)
        attempts = self._attempts(normalized, city)
        if not attempts:
            return GeocodeResult(status=GeocodeStatus.NOT_FOUND, searched_as=normalized.query)

        # Cache key includes the city: the same line can resolve differently
        # when the user changes scope.
        cache_key = f"{(normalized.query or '').strip().lower()}|{(city or '').strip().lower()}"
        cached = self._batch_cache.get(cache_key)
        if cached is not None and cached.status != GeocodeStatus.ERROR:
            step = self._new_step(index, original, normalized)
            if step is not None:
                step.kind = "cache"
                step.scope_city = city or normalized.city
                step.from_cache = True
                step.outcome = cached.status.value
                step.notes.append("served from the batch cache, no provider call")
                self._trace.append(step)
            return cached

        last: Optional[GeocodeResult] = None
        for attempt in attempts:
            raw = await self._run_attempt(attempt, normalized.city or city)
            searched_as = attempt.searched_as

            step = self._new_step(index, original, normalized)
            if step is not None:
                step.attempt_number = len(self._trace) + 1
                if attempt.text:
                    step.kind = "text"
                    step.query = attempt.text
                else:
                    step.kind = "structured"
                    step.structured_street = attempt.structured_street
                    step.request_city = attempt.city or city
                step.scope_city = city or normalized.city
                step.asked_house = attempt.asked_house
                step.candidates = [
                    _TraceCandidate(
                        display_name=c.display_name,
                        house_number=_house_number_of(c),
                        city=_found_city(c),
                    )
                    for c in raw.candidates
                ]
                step.outcome = raw.status.value

            if raw.status == GeocodeStatus.ERROR:
                # A rate-limit or setup failure will not improve by trying the
                # other forms, and burning attempts makes it worse.
                if step is not None:
                    step.notes.append(f"provider error: {raw.error_message or 'unknown'}")
                    self._trace.append(step)
                return replace(raw, searched_as=searched_as)

            result = self._classify(
                raw, normalized, city or normalized.city, searched_as,
                notes=step.notes if step is not None else None,
                asked_house=attempt.asked_house,
            )
            if step is not None:
                step.outcome = result.status.value
                self._trace.append(step)
            # Keep the most informative answer seen so far. Ties keep the earlier
            # attempt, which is the one most likely to carry a drop notice.
            if last is None or _SPECIFICITY[result.status] > _SPECIFICITY[last.status]:
                last = result

            if result.status == GeocodeStatus.RESOLVED or _is_final_ambiguity(
                result, attempt.asked_house
            ):
                # Reached house level with the house we asked for: nothing left
                # to improve on. A house *mismatch* is not final, because the
                # next spelling of the address may well match exactly.
                self._batch_cache[cache_key] = result
                return result
            if result.status == GeocodeStatus.PARTIAL and not normalized.house:
                # The input asked for a street only, so this is the best answer
                # the remaining attempts could give.
                self._batch_cache[cache_key] = result
                return result
            # Not found, or partial while an explicit house number is known:
            # the next form may resolve it.

        # Every candidate the provider had was outside the active city. Rather
        # than stop here and offer a retry that cannot succeed, check whether the
        # street itself exists in that city: an approximate pin on the right
        # street is more useful, and a street that does not exist settles the
        # question outright. The check is on the answer we kept, not on any
        # single attempt, so a later in-scope answer is never thrown away.
        if last is not None and last.all_out_of_scope:
            street_attempt = self._street_only_attempt(normalized, city)
            if street_attempt is not None:
                outcome = await self._street_only_result(
                    street_attempt, normalized, city, index, last
                )
                if outcome is not None:
                    self._batch_cache[cache_key] = outcome
                    return outcome

        # Nothing resolved to house level. Errors are never cached, so a retry
        # after a rate limit can still succeed.
        result = last or GeocodeResult(status=GeocodeStatus.NOT_FOUND, searched_as=normalized.query)
        if result.status != GeocodeStatus.ERROR:
            self._batch_cache[cache_key] = result
        return result

    async def _street_only_result(
        self,
        attempt: _Attempt,
        normalized: NormalizedAddress,
        city: Optional[str],
        index: int,
        previous: Optional[GeocodeResult] = None,
    ) -> Optional[GeocodeResult]:
        """Look the street up on its own inside the active city.

        Returns ``partial`` with an approximate pin when the street is there, and
        ``not_found`` with a definite message when it is not. Both carry no
        retry city, because a second run in the same city would reach the same
        answer. ``previous`` is the out-of-scope answer being replaced, whose
        drop notice is kept so the user can still see where the matches were.
        """
        raw = await self._run_attempt(attempt, normalized.city or city)
        scope_city = attempt.city or city or normalized.city

        step = self._new_step(index, normalized.original, normalized)
        if step is not None:
            step.attempt_number = len(self._trace) + 1
            step.kind = "street-only"
            step.structured_street = attempt.structured_street
            step.request_city = attempt.city
            step.scope_city = scope_city
            step.asked_house = None
            step.candidates = [
                _TraceCandidate(
                    display_name=c.display_name,
                    house_number=_house_number_of(c),
                    city=_found_city(c),
                )
                for c in raw.candidates
            ]

        if raw.status == GeocodeStatus.ERROR:
            if step is not None:
                step.notes.append(f"provider error: {raw.error_message or 'unknown'}")
                self._trace.append(step)
            return None

        in_scope = [
            c
            for c in raw.candidates
            if scope_city is None or matches_city(c.address, scope_city)
        ]
        # A street-only query can still answer with a house, and that house is
        # not the one asked for, so it must not be presented as the address.
        representative = _representative(in_scope) if in_scope else None

        if representative is not None:
            message = (
                f"{APPROXIMATE_MATCH_MESSAGE} "
                f"{scope_city} has this street, but not the house"
                f"{' ' + normalized.house if normalized.house else ''}."
            )
            note = f"street exists in {scope_city}, pin is approximate"
            result = GeocodeResult(
                status=GeocodeStatus.PARTIAL,
                coordinate=Coordinate(representative.latitude, representative.longitude),
                display_name=representative.display_name,
                candidates=[representative],
                error_message="Found the street but not the house",
                message=message,
                searched_as=attempt.searched_as,
                house=normalized.house,
                unit=normalized.unit,
                unit_kind=normalized.unit_kind,
                unit_inferred=normalized.unit_inferred,
                found_house=_house_number_of(representative),
                found_city=_found_city(representative),
                # Still flagged: the pin is a street, not the address asked for.
                needs_check=True,
                needs_check_reason=message,
            )
            outcome = result.status.value
        else:
            no_such = NO_SUCH_ADDRESS_MESSAGE.format(city=scope_city)
            note = f"street {attempt.structured_street!r} not present in {scope_city}"
            result = GeocodeResult(
                status=GeocodeStatus.NOT_FOUND,
                searched_as=attempt.searched_as,
                error_message=no_such,
                message=no_such,
                house=normalized.house,
                unit=normalized.unit,
                unit_kind=normalized.unit_kind,
                unit_inferred=normalized.unit_inferred,
                found_city=scope_city,
                needs_check=previous.needs_check if previous else False,
                needs_check_reason=previous.needs_check_reason if previous else None,
            )
            outcome = result.status.value

        # The out-of-scope notice is the reason this row is not exact, so it is
        # kept on whichever answer replaces it.
        if previous is not None:
            result = replace(
                result,
                found_house=result.found_house or previous.found_house,
                # `found_city` is only carried over for a row that found nothing
                # in the city. A pin that really is in the city keeps its own
                # city, otherwise the row would look out of scope again.
                found_city=(
                    previous.found_city
                    if result.status == GeocodeStatus.NOT_FOUND
                    else result.found_city
                ),
                dropped_candidates=previous.dropped_candidates,
                scope_message=previous.scope_message,
                needs_check=True,
                needs_check_reason=result.needs_check_reason or previous.needs_check_reason,
            )

        if step is not None:
            step.notes.append(note)
            step.outcome = outcome
            self._trace.append(step)
        return result

    # -- batch --------------------------------------------------------------

    async def geocode_batch(
        self, items: List[BatchGeocodeRequestItem], city: Optional[str] = None
    ) -> List[BatchGeocodeResponseItem]:
        if len(items) > 5:
            raise ValueError("Batch size cannot exceed 5")

        self._trace = []

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
            resolved[key] = await self.geocode_one(item.original, item.trimmed, city, item.index)

        results: List[BatchGeocodeResponseItem] = []
        for item in items:
            res = resolved[item.trimmed.strip().lower()]
            results.append(self._to_response_item(item, res))
        self._apply_list_checks(results, city)
        return results

    def _apply_list_checks(
        self, results: List[BatchGeocodeResponseItem], city: Optional[str]
    ) -> None:
        """Flag rows that look wrong in the context of the whole list.

        A match can be internally consistent and still be wrong: the right
        street in the wrong town, or hundreds of kilometres from every other
        stop. Those only show up once the list is seen together.
        """
        # A city named in the list scopes the rows that did not get one.
        scope_city = city or _detected_city(results)

        suggestion = infer_dominant_city(results) if not city else None
        dominant = suggestion.city if suggestion else (scope_city or None)
        if suggestion is not None:
            self._city_suggestion = suggestion

        for row in results:
            if row.status != "resolved":
                continue
            if scope_city and row.found_city and not _same_city(row.found_city, scope_city):
                row.needs_check = True
                row.needs_check_reason = (
                    f"Found in {row.found_city}, outside {scope_city}."
                )
                if dominant:
                    row.retry_city = dominant

        for key, distance in flag_distance_outliers(results, max_km=self._max_outlier_km).items():
            row = next((r for r in results if r.original.strip().lower() == key), None)
            if row is not None:
                row.needs_check = True
                # An out-of-city reason already explains the problem better than
                # a distance would, so it is never overwritten.
                row.needs_check_reason = row.needs_check_reason or describe_outlier(
                    distance, self._max_outlier_km
                )

    @property
    def city_suggestion(self) -> Optional[CitySuggestion]:
        """City the last batch looked like it was about, for the City field."""
        return self._city_suggestion

    def reset(self) -> None:
        """Forget per-batch state. The cache is kept: it is still valid."""
        self._city_suggestion = None
        self._trace = []

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
            found_house=res.found_house,
            found_city=res.found_city,
            needs_check=res.needs_check,
            needs_check_reason=res.needs_check_reason,
            retry_city=res.retry_city,
        )


def apply_whole_list_checks(
    results: List[BatchGeocodeResponseItem],
    city: Optional[str],
    max_outlier_km: float = DEFAULT_OUTLIER_KM,
) -> None:
    """Re-run the list checks over results assembled from several batches.

    geocode_batch() checks each 5-row chunk on its own, so a row that is only
    wrong in the context of the whole list - one stop resolved in another
    city, or far from every other stop - can slip through the small chunk it
    happened to land in (distance outliers need at least three points to
    compare against). This adds those flags back. It never removes a
    needs_check flag or overwrites a reason: per-chunk checks keep their
    wording.

    The scope is the explicit city if one was given, otherwise the city the
    resolved rows (mostly) agree on - the same dominance rule the API suggests
    for the City field. The city the *input lines* name is not used here: one
    line saying another city must not re-scope a list.
    """
    suggestion = infer_dominant_city(results) if not city else None
    scope = city or (suggestion.city if suggestion else None)
    dominant = suggestion.city if suggestion else scope

    for row in results:
        if row.status != "resolved":
            continue
        if scope and row.found_city and not _same_city(row.found_city, scope):
            row.needs_check = True
            row.needs_check_reason = row.needs_check_reason or (
                f"Found in {row.found_city}, outside {scope}."
            )
            if dominant and row.retry_city is None:
                row.retry_city = dominant

    for key, distance in flag_distance_outliers(results, max_km=max_outlier_km).items():
        row = next((r for r in results if r.original.strip().lower() == key), None)
        if row is not None:
            row.needs_check = True
            row.needs_check_reason = row.needs_check_reason or describe_outlier(
                distance, max_outlier_km
            )


async def geocode_in_batches(
    service: GeocodeService,
    items: List[BatchGeocodeRequestItem],
    city: Optional[str] = None,
    batch_size: int = 5,
    on_batch=None,
) -> List[BatchGeocodeResponseItem]:
    """Geocode a list of items in provider-sized batches.

    The geocoder accepts at most 5 items per call, so callers that work with
    longer lists all share this chunking instead of reimplementing it.
    geocode_batch() deduplicates identical lines only within a single chunk,
    so duplicates pass through here untouched, and results keep the caller's
    order and indexes. on_batch, when given, is called after each completed
    chunk with (start_index, chunk_items, batch_results) - it sees the
    service's per-batch state (trace, city suggestion) for that chunk.

    Per-chunk checks see only their chunk, so the whole list is checked again
    once every chunk has run; a far-away or wrong-city stop is flagged even
    when its own chunk was too small or too uniform to notice.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    results: List[BatchGeocodeResponseItem] = []
    for start in range(0, len(items), batch_size):
        chunk = items[start : start + batch_size]
        batch = await service.geocode_batch(chunk, city=city)
        results.extend(batch)
        if on_batch is not None:
            on_batch(start, chunk, batch)
    apply_whole_list_checks(
        results,
        city,
        max_outlier_km=getattr(service, "_max_outlier_km", DEFAULT_OUTLIER_KM),
    )
    return results