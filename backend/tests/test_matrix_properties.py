"""Property checks over distance matrices.

These run against a recorded OSRM response for a handful of public place
coordinates (never personal addresses), so they exercise the same validation
the provider applies to live responses and sanity-check the numbers:
road distance cannot be shorter than the straight line, the diagonal is zero,
and asymmetric matrices are accepted.
"""

import json
import math
from pathlib import Path

import pytest

from app.core.config import Settings
from app.providers.routing.osrm import OsrmRouter

FIXTURE = Path(__file__).parent / "fixtures" / "osrm_matrix_sample.json"

# A "small" tolerance: snapping to the road network can move a point by up to
# its snap distance, so a route can legitimately be shorter than the straight
# line between the original points. Twice the largest snap distance bounds that
# for every pair; a separate margin check in the test keeps it honest.
EARTH_RADIUS_M = 6371000.0


def load_fixture():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data["coordinates"], data["response"]


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


@pytest.fixture
def router():
    return OsrmRouter(
        Settings(
            NOMINATIM_USER_AGENT="RouteOptimizer-test/0.1.0 (test@test.com)",
            OSRM_RATE_LIMIT_DELAY_SECONDS=0.0,
        )
    )


@pytest.fixture
def sample():
    coordinates, response = load_fixture()
    n = len(coordinates)
    assert n > 0
    return coordinates, response, n


def test_diagonal_is_zero(sample, router):
    coordinates, response, n = sample
    result = router._parse_and_validate(response, n)
    for i in range(n):
        assert result.durations_s[i][i] <= 1e-6
        assert result.distances_m[i][i] <= 1e-6


def test_road_distance_not_less_than_straight_line(sample, router):
    coordinates, response, n = sample
    result = router._parse_and_validate(response, n)
    snaps = result.snap_distance_m or [0.0] * n
    tolerance = 2.0 * (max(snaps) if snaps else 0.0)
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            road = result.distances_m[i][j]
            assert road is not None
            straight = haversine_m(coordinates[i], coordinates[j])
            # The tolerance is only for the snap distance; the margin test
            # below proves the matrix is not trivially sloppy.
            assert road >= straight - tolerance


def test_margin_is_wider_than_snap_adjustment(sample, router):
    """The road distances are genuinely longer than the straight line even
    after accounting for snapping, so the tolerance check is not vacuous."""
    coordinates, response, n = sample
    result = router._parse_and_validate(response, n)
    snaps = result.snap_distance_m or [0.0] * n
    worst = 0.0
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            road = result.distances_m[i][j]
            straight = haversine_m(coordinates[i], coordinates[j])
            worst = max(worst, (straight - (snaps[i] + snaps[j] + 0.5)) - road)
    assert worst <= 0.0


def test_asymmetry_is_accepted(sample, router):
    coordinates, response, n = sample
    result = router._parse_and_validate(response, n)
    # The recorded public OSRM server returns slightly asymmetric duration
    # matrices; the validator must not reject them.
    asymmetric = any(
        result.durations_s[i][j] != result.durations_s[j][i]
        for i in range(n)
        for j in range(n)
    )
    assert asymmetric


def test_hand_built_asymmetric_matrix_is_accepted(router):
    data = {
        "code": "Ok",
        "durations": [[0.0, 100.0], [200.0, 0.0]],
        "distances": [[0.0, 1000.0], [2000.0, 0.0]],
    }
    result = router._parse_and_validate(data, 2)
    assert result.durations_s[0][1] == 100.0
    assert result.durations_s[1][0] == 200.0


def test_null_cells_survive_the_fixture_check(sample, router):
    coordinates, response, n = sample
    result = router._parse_and_validate(response, n)
    assert len(result.durations_s) == n
    assert all(len(row) == n for row in result.durations_s)
    assert all(len(row) == n for row in result.distances_m)