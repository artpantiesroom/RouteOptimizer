"""Measure live geocoding quality over a text file of addresses.

Reads one address per line, runs the same pipeline the API uses (normalize ->
fallback chain -> city scoping), and prints per-status counts plus a line per
address so results can be eyeballed.

This hits the real geocoder. Nominatim allows at most 1 request per second and
forbuses bulk use, so keep the input file small and personal.

The configured `NOMINATIM_USER_AGENT` is used. A generic or placeholder one is
rejected with HTTP 403, so use a real identifying string.

Usage:
    .venv/bin/python scripts/measure_quality.py addresses.txt
    .venv/bin/python scripts/measure_quality.py addresses.txt --city Київ --json out.json

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
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings  # noqa: E402
from app.providers.geocoding.nominatim import NominatimGeocoder  # noqa: E402
from app.services.geocode_service import (  # noqa: E402
    BatchGeocodeRequestItem,
    GeocodeService,
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


def summarize(result) -> dict:
    candidates = list(result.candidates or [])
    first_candidate = None
    if candidates:
        first = candidates[0]
        first_candidate = first.get("display_name") if isinstance(first, dict) else str(first)
    status = result.status.value if hasattr(result.status, "value") else result.status
    return {
        "status": str(status),
        "searched_as": result.searched_as,
        "house": result.house,
        "unit": result.unit,
        "unit_kind": result.unit_kind,
        "unit_inferred": result.unit_inferred,
        "display_name": result.display_name,
        "coordinate": result.coordinate,
        "dropped_candidates": result.dropped_candidates,
        "scope_message": result.scope_message,
        "message": result.message,
        "error_kind": result.error_kind.value if result.error_kind else None,
        "candidate_count": len(candidates),
        "candidates": [
            c.get("display_name") if isinstance(c, dict) else str(c) for c in candidates
        ],
        "first_candidate": first_candidate,
    }


async def run(addresses: List[str], city: str | None) -> List[dict]:
    service = GeocodeService(NominatimGeocoder(get_settings()))
    rows: List[dict] = []

    for start in range(0, len(addresses), BATCH_SIZE):
        chunk = addresses[start : start + BATCH_SIZE]
        items = [
            BatchGeocodeRequestItem(index=i, original=text, trimmed=text)
            for i, text in enumerate(chunk)
        ]
        results = await service.geocode_batch(items, city=city)
        for text, result in zip(chunk, results):
            row = {"address": text}
            row.update(summarize(result))
            rows.append(row)
            print(f"{str(row['status']):11} | {text}")
            detail = result.display_name or result.message or ""
            if row["dropped_candidates"]:
                detail = f"{detail} [{row['scope_message']}]"
            if detail:
                print(f"{'':11} | {detail}")
        print(f"  ({min(start + BATCH_SIZE, len(addresses))}/{len(addresses)} done)")

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path, help="text file with one address per line")
    parser.add_argument("--city", default=None, help="scope results to this city")
    parser.add_argument("--json", type=Path, default=None, help="write rows as JSON")
    args = parser.parse_args()

    addresses = read_addresses(args.file)
    print(f"Checking {len(addresses)} addresses (city: {args.city or 'none'})\n")

    rows = asyncio.run(run(addresses, args.city))

    counts = Counter(row["status"] for row in rows)
    total = len(rows)
    print("\nSummary")
    for status, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {status:11} {count:4}  {count / total:6.1%}")
    print(f"  {'total':11} {total:4}")

    inferred = sum(1 for row in rows if row["unit_inferred"])
    if inferred:
        print(f"\n{inferred} address(es) had an inferred unit - check those.")
    dropped = sum(1 for row in rows if row["dropped_candidates"])
    if dropped:
        print(f"{dropped} address(es) had out-of-scope matches dropped.")

    if args.json:
        args.json.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nWrote {args.json}")


if __name__ == "__main__":
    main()