"""Measure live geocoding quality over a text file of addresses.

Reads one address per line, runs the same pipeline the API uses (normalize ->
fallback chain -> city scoping -> list-level checks), and prints per-status
counts plus a line per address so results can be eyeballed.

This hits the real geocoder. Nominatim allows at most 1 request per second and
forbids bulk use, so keep the input file small and personal.

The configured `NOMINATIM_USER_AGENT` is used. A generic or placeholder one is
rejected with HTTP 403, so use a real identifying string.

Usage:
    .venv/bin/python scripts/measure_quality.py addresses.txt
    .venv/bin/python scripts/measure_quality.py addresses.txt --city Київ
    .venv/bin/python scripts/measure_quality.py addresses.txt --trace
    .venv/bin/python scripts/measure_quality.py addresses.txt --json out.json

`--trace` prints every fallback attempt with the request that was sent, the
scope applied, the candidates that came back and the reason each one was
accepted or rejected. Use it when a "resolved" row turns out to be wrong.

The input file takes one address per line; blank lines and lines starting with
`#` are skipped.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings  # noqa: E402
from app.providers.geocoding.nominatim import NominatimGeocoder  # noqa: E402
from app.services.address_normalizer import house_numbers_equal  # noqa: E402
from app.services.geocode_service import (  # noqa: E402
    DEFAULT_OUTLIER_KM,
    BatchGeocodeRequestItem,
    GeocodeService,
    geocode_in_batches,
)

BATCH_SIZE = 5  # same limit the API enforces
MAX_LINES = 500


def read_addresses(path: Path) -> List[str]:
    addresses = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    addresses = [a for a in addresses if a and not a.startswith("#")]
    if not addresses:
        raise SystemExit(f"No addresses found in {path}")
    if len(addresses) > MAX_LINES:
        print(
            f"Warning: {len(addresses)} addresses. Nominatim is rate limited to "
            f"1 req/s; this will take a long time.",
            file=sys.stderr,
        )
    return addresses


def osm_link(coordinate: Any) -> Optional[str]:
    """A map link for a resolved pin, so a wrong one can be eyeballed."""
    if not isinstance(coordinate, dict):
        return None
    lat, lon = coordinate.get("latitude"), coordinate.get("longitude")
    if lat is None or lon is None:
        return None
    return f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}"


def candidate_name(candidate: Any) -> str:
    if isinstance(candidate, dict):
        return str(candidate.get("display_name") or "")
    return str(candidate)


def summarize(result) -> dict:
    candidates = list(result.candidates or [])
    status = result.status.value if hasattr(result.status, "value") else result.status
    return {
        "status": str(status),
        "searched_as": result.searched_as,
        "house": result.house,
        "found_house": result.found_house,
        "found_city": result.found_city,
        "unit": result.unit,
        "unit_kind": result.unit_kind,
        "unit_inferred": result.unit_inferred,
        "display_name": result.display_name,
        "coordinate": result.coordinate,
        "osm_link": osm_link(result.coordinate),
        "dropped_candidates": result.dropped_candidates,
        "scope_message": result.scope_message,
        "message": result.message,
        "error_kind": result.error_kind.value if result.error_kind else None,
        "needs_check": result.needs_check,
        "needs_check_reason": result.needs_check_reason,
        "retry_city": result.retry_city,
        "candidate_count": len(candidates),
        "candidates": [candidate_name(c) for c in candidates],
    }


def print_trace(steps: List[dict]) -> None:
    for step in steps:
        if step["kind"] == "cache":
            print(f"      trace: cached -> {step['outcome']}")
            continue
        request = (
            f"structured street={step['structured_street']!r} city={step['request_city']!r}"
            if step["kind"] == "structured"
            else f"query={step['query']!r}"
        )
        print(f"      trace #{step['attempt']} {step['kind']}: {request}")
        print(f"        scope={step['scope_city'] or 'none'} asked_house={step['asked_house'] or '-'}")
        for candidate in step["candidates"]:
            print(
                f"        got: house={candidate['house_number'] or '-'} "
                f"city={candidate['city'] or '-'} {candidate['display_name']}"
            )
        if not step["candidates"]:
            print("        got: no candidates")
        for note in step["notes"]:
            print(f"        -> {note}")
        print(f"        outcome: {step['outcome']}")


def print_row(row: dict, street_level: bool = False) -> None:
    status = str(row["status"])
    if row["needs_check"]:
        status = "needs check"
    print(f"{status:11} | {row['address']}")
    # Only a row that stayed unresolved was judged against the parsed house.
    # A resolved row was validated against the number its own attempt sent, so
    # comparing it with `house` here would report a match as a mismatch.
    if row["status"] != "resolved" and row["found_house"] and row["house"]:
        print(f"{'':11} | house: asked {row['house']}, found {row['found_house']}")
    if row["found_city"]:
        print(f"{'':11} | city: found in {row['found_city']}")
    if row["needs_check_reason"]:
        print(f"{'':11} | {row['needs_check_reason']}")
    if row["retry_city"]:
        print(f"{'':11} | retry with city: {row['retry_city']}")
    detail = row["display_name"] or row["message"] or ""
    if row["dropped_candidates"]:
        detail = f"{detail} [{row['scope_message']}]"
    if detail:
        print(f"{'':11} | {detail}")
    if row["status"] == "ambiguous" and row["candidates"]:
        print(f"{'':11} | candidates: {'; '.join(row['candidates'])}")
    if street_level and row["status"] != "resolved":
        print(f"{'':11} | asked: {', '.join(row['asked_houses']) or '(street only)'}")
        print(f"{'':11} | provider: {row['verdict_text']}")
        shown = [
            c
            for c in row["house_candidates"]
            if c["is_requested"] or c["in_scope"] is not False
        ][:4]
        for candidate in shown:
            where = (
                "no scope applied"
                if candidate["in_scope"] is None
                else "in scope" if candidate["in_scope"] else "out of scope"
            )
            mark = "requested" if candidate["is_requested"] else "other house"
            print(
                f"{'':11} |   {candidate['house_number']} in {candidate['city'] or '-'} "
                f"({where}, {mark}): {candidate['display_name'][:50]}"
            )
        if not row["house_candidates"] and row["houseless_candidate_count"]:
            print(f"{'':11} |   {row['houseless_candidate_count']} candidate(s) with no house")
    if row["osm_link"]:
        print(f"{'':11} | {row['osm_link']}")


# The five causes of a non-resolved row, most specific first. A row gets
# exactly one, so the counts always add up to the non-resolved rows.
VERDICTS = {
    "found_in_scope_but_rejected": "OSM has the house in scope but we rejected it (a bug)",
    "found_outside_scope": "the requested house exists only outside the scope",
    "different_house": "only a different house number exists",
    "street_only": "the street exists, no house number anywhere",
    "nothing_returned": "the provider returned nothing for our queries",
}


def house_candidate_report(steps: List[dict], address: str) -> dict:
    """What the provider itself said about this address, ignoring our rules.

    A non-resolved row has several very different causes and they need
    separating: the map data may simply lack the house, or OSM may have it and
    our own query or city filter threw it away. The trace is the only place
    that difference is visible, because our filters run before the row exists.

    A candidate counts as "the requested house" when it equals any number an
    attempt actually sent, so "51/53" answers an attempt that asked "51-53".
    """
    asked_houses = [
        step["asked_house"]
        for step in steps
        if step.get("address") == address and step.get("kind") != "cache"
    ]
    asked_houses = [h for h in asked_houses if h]

    with_house: List[dict] = []
    without_house = 0
    for step in steps:
        if step.get("address") != address or step.get("kind") == "cache":
            continue
        for candidate in step["candidates"]:
            if not candidate["house_number"]:
                without_house += 1
                continue
            in_scope = (
                None
                if not step["scope_city"]
                else candidate["city"] == step["scope_city"]
            )
            with_house.append(
                {
                    "house_number": candidate["house_number"],
                    "city": candidate["city"],
                    "display_name": candidate["display_name"],
                    "in_scope": in_scope,
                    "is_requested": any(
                        house_numbers_equal(asked, candidate["house_number"])
                        for asked in asked_houses
                    ),
                }
            )

    requested = [c for c in with_house if c["is_requested"]]
    requested_in_scope = [c for c in requested if c["in_scope"] is not False]
    requested_outside = [c for c in requested if c["in_scope"] is False]
    other_houses = [c for c in with_house if not c["is_requested"]]
    other_in_scope = [c for c in other_houses if c["in_scope"] is not False]

    if requested_in_scope:
        # The provider gave us the right house inside the active city and the
        # row is still not resolved, so one of our own rules is at fault.
        verdict = "found_in_scope_but_rejected"
    elif requested_outside:
        verdict = "found_outside_scope"
    elif other_in_scope:
        verdict = "different_house"
    elif with_house:
        verdict = "found_outside_scope"
    elif without_house:
        verdict = "street_only"
    else:
        verdict = "nothing_returned"

    return {
        "verdict": verdict,
        "verdict_text": VERDICTS[verdict],
        "asked_houses": asked_houses,
        "house_candidates": with_house,
        "house_candidate_count": len(with_house),
        "houseless_candidate_count": without_house,
    }


async def run(
    addresses: List[str], city: str | None, trace: bool, street_level: bool
) -> tuple[List[dict], Any]:
    # The street-level report reads the trace, so it needs it collected even when
    # the full trace is not being printed.
    service = GeocodeService(NominatimGeocoder(get_settings()), trace=trace or street_level)
    rows: List[dict] = []
    suggestions: List[Any] = []

    items = [
        BatchGeocodeRequestItem(index=i, original=text, trimmed=text)
        for i, text in enumerate(addresses)
    ]

    def on_batch(start: int, chunk: List[Any], batch: List[Any]) -> None:
        if service.city_suggestion is not None:
            suggestions.append(service.city_suggestion)
        steps = service.trace_as_dicts()
        if trace:
            print_trace(steps)
        for item, result in zip(chunk, batch):
            text = item.original
            row = {"address": text}
            row.update(summarize(result))
            if street_level:
                row.update(house_candidate_report(steps, text))
            rows.append(row)
            print_row(row, street_level)
        print(f"  ({min(start + BATCH_SIZE, len(addresses))}/{len(addresses)} done)")

    await geocode_in_batches(service, items, city=city, on_batch=on_batch)

    return rows, suggestions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path, help="text file with one address per line")
    parser.add_argument(
        "--city", default=None, help="scope results to this city (overrides any city in the text)"
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="print every fallback attempt, its scope, its candidates and why they were accepted",
    )
    parser.add_argument(
        "--street-level-report",
        action="store_true",
        help="for every non-resolved row, report whether the provider itself returned "
        "any candidate with a house number, to separate 'OSM lacks the house' from "
        "'our query or scope lost the house'",
    )
    parser.add_argument("--json", type=Path, default=None, help="write rows as JSON")
    args = parser.parse_args()

    addresses = read_addresses(args.file)
    scope = args.city or "none (taken from the addresses)"
    print(f"Checking {len(addresses)} addresses (city: {scope})\n")

    rows, suggestions = asyncio.run(
        run(addresses, args.city, args.trace, args.street_level_report)
    )

    counts = Counter(row["status"] for row in rows)
    total = len(rows)
    needs_check = sum(1 for row in rows if row["needs_check"])
    # A resolved row that was flagged is not an ok row, so it is not counted twice.
    resolved = sum(
        1 for row in rows if row["status"] == "resolved" and not row["needs_check"]
    )

    print("\nSummary")
    print("  status as returned:")
    for status, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"    {status:11} {count:4}  {count / total:6.1%}")
    print(f"    {'total':11} {total:4}")
    # The status above is what the API said. A resolved row can still be flagged,
    # so the verdict below only counts the rows that are both.
    print("\n  resolved (ok) {0:4}  {1:6.1%}".format(resolved, resolved / total))
    print("  needs check   {0:4}  {1:6.1%}".format(needs_check, needs_check / total))
    if needs_check:
        print("  Needs a check means the match sits in another city or too far away.")

    if suggestions:
        seen = {(s.city, s.resolved_count, s.total_confident) for s in suggestions}
        for suggested, count, confident in sorted(seen):
            print(
                f"\n  Suggested city: {suggested} "
                f"({count}/{confident} confident rows resolved there)"
            )

    inferred = sum(1 for row in rows if row["unit_inferred"])
    if inferred:
        print(f"\n{inferred} address(es) had an inferred unit - check those.")
    # A resolved row matched the number its own attempt sent, so only rows that
    # stayed unresolved can be a genuine house mismatch.
    mismatched = [
        row
        for row in rows
        if row["status"] != "resolved"
        and row["found_house"]
        and row["house"]
        and not house_numbers_equal(row["house"], row["found_house"])
    ]
    if mismatched:
        print(f"{len(mismatched)} address(es) resolved to a different house number:")
        for row in mismatched:
            print(f"  {row['address']}: asked {row['house']}, found {row['found_house']}")
    dropped = sum(1 for row in rows if row["dropped_candidates"])
    if dropped:
        print(f"{dropped} address(es) had out-of-scope matches dropped.")
    print(f"Distance outlier limit: {DEFAULT_OUTLIER_KM:g} km from the other stops.")

    if args.street_level_report:
        unresolved = [row for row in rows if row["status"] != "resolved"]
        counts = Counter(row["verdict"] for row in unresolved)
        print("\nWhy the non-resolved rows are not resolved")
        for verdict in VERDICTS:
            if counts.get(verdict):
                print(f"  {counts[verdict]:4}  {VERDICTS[verdict]}")
                for row in unresolved:
                    if row["verdict"] == verdict:
                        print(f"        {row['address']}")
        print(f"  {len(unresolved):4}  non-resolved rows in total")
        assert sum(counts.values()) == len(unresolved)

    if args.json:
        args.json.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nWrote {args.json}")


if __name__ == "__main__":
    main()