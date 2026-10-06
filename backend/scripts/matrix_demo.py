"""Calculate a driving-time/distance matrix over a text file of addresses.

Reads one address per line, geocodes each with the same pipeline the API uses
(the first usable address becomes the start point), then requests the matrix
from the configured routing service and prints durations (minutes) and
distances (km) plus any "unreachable"/"far from road" problems.

Addresses that need a check (found in another city or flagged as distance
outliers) or that did not resolve cannot be a point yet, so they are listed
with the reason and excluded rather than geocoded into the route; at least 2
usable points must remain.

This hits the real geocoder and the real routing service. The public OSRM demo
server allows about one request per second, is for non-commercial use, gives no
uptime guarantee and must be credited - keep the input file small and personal.

A far-from-road problem means the route matrix may be slightly off, but it is
still returned; "unreachable" means that pair has no road route at all.

Usage:
    .venv/bin/python scripts/matrix_demo.py addresses.txt
    .venv/bin/python scripts/matrix_demo.py addresses.txt --city Київ

The input file takes one address per line; blank lines and lines starting with
`#` are skipped. The first usable address is the start point.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings  # noqa: E402
from app.domain.models import Coordinate  # noqa: E402
from app.domain.routing import MatrixPoint  # noqa: E402
from app.providers.geocoding.nominatim import NominatimGeocoder  # noqa: E402
from app.providers.routing.osrm import OsrmRouter  # noqa: E402
from app.services.geocode_service import (  # noqa: E402
    BatchGeocodeRequestItem,
    GeocodeService,
    geocode_in_batches,
)
from app.services.matrix_service import MatrixService  # noqa: E402

MAX_LINES = 50


def read_addresses(path: Path) -> List[str]:
    addresses = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    addresses = [a for a in addresses if a and not a.startswith("#")]
    if not addresses:
        raise SystemExit(f"No addresses found in {path}")
    if len(addresses) > MAX_LINES:
        print(
            f"Warning: {len(addresses)} addresses. The routing service is rate "
            f"limited; this will take a long time.",
            file=sys.stderr,
        )
    return addresses


async def geocode(service: GeocodeService, texts: List[str], city: str | None) -> List[MatrixPoint]:
    items = [
        BatchGeocodeRequestItem(index=i, original=text, trimmed=text)
        for i, text in enumerate(texts)
    ]
    results = await geocode_in_batches(service, items, city=city)
    points: List[MatrixPoint] = []
    for text, result in zip(texts, results):
        status = result.status.value if hasattr(result.status, "value") else result.status
        if status != "resolved":
            # Not a hard stop: the row simply cannot be a point yet. It is
            # reported with its reason and left out, like a needs-check row.
            reason = result.message or f"status {status}"
            print(f"Excluding {text!r}: not resolved ({reason})")
            continue
        if result.needs_check:
            # Listed and excluded, never silently dropped.
            reason = result.needs_check_reason or "needs a check"
            print(f"Excluding {text!r}: {reason}")
            continue
        if result.coordinate is None:
            raise SystemExit(f"Cannot geocode: {text!r} (no coordinate)")
        # The geocoder returns coordinates as plain dicts; the routing layer
        # expects a Coordinate.
        coordinate = result.coordinate
        if isinstance(coordinate, dict):
            coordinate = Coordinate(
                latitude=float(coordinate["latitude"]),
                longitude=float(coordinate["longitude"]),
            )
        points.append(
            MatrixPoint(
                id="start" if not points else str(len(points) - 1),
                coordinate=coordinate,
            )
        )
    if len(points) < 2:
        raise SystemExit(
            "Need at least 2 usable points (start plus at least one stop) "
            "after excluding flagged rows."
        )
    return points


def minutes(seconds: float) -> str:
    return f"{seconds / 60:.1f}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path, help="text file with one address per line")
    parser.add_argument(
        "--city", default=None, help="scope results to this city (overrides any city in the text)"
    )
    args = parser.parse_args()

    addresses = read_addresses(args.file)
    scope = args.city or "none (taken from the addresses)"
    print(f"Geocoding {len(addresses)} addresses (city: {scope})\n")

    settings = get_settings()
    geocoder_service = GeocodeService(NominatimGeocoder(settings))
    points = asyncio.run(geocode(geocoder_service, addresses, args.city))

    service = MatrixService(
        OsrmRouter(settings), max_points=settings.MATRIX_MAX_POINTS
    )
    result = asyncio.run(service.compute(points))

    names = ["start"] + [f"stop {i}" for i in range(len(points) - 1)]
    matrix = result.matrix

    print(f"\nDriving matrix ({matrix.provider}, profile {matrix.profile}); "
          f"{len(points)} points")
    print(f"{'':12}", "  ".join(f"{name:>10}" for name in names))
    for i, name_from in enumerate(names):
        dur = "  ".join(
            f"{minutes(v):>10}" if v is not None else f"{'--':>10}"
            for v in matrix.durations_s[i]
        )
        print(f"{name_from:12} {dur}   (minutes)")
    print()
    print(f"{'':12}", "  ".join(f"{name:>10}" for name in names))
    for i, name_from in enumerate(names):
        dist = "  ".join(
            f"{v / 1000:>10.1f}" if v is not None else f"{'--':>10}"
            for v in matrix.distances_m[i]
        )
        print(f"{name_from:12} {dist}   (km)")

    if result.problems:
        print("\nProblems:")
        for problem in result.problems:
            print(f"  {problem.id}: {problem.kind} - {problem.message}")
    else:
        print("\nNo problems.")

    print(
        "\nDurations are typical driving times without live traffic. "
        "Map data (c) OpenStreetMap contributors, routes by OSRM."
    )


if __name__ == "__main__":
    main()
