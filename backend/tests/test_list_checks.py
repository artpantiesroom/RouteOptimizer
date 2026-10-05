"""List-level checks: dominant city inference and distance outliers.

Fixtures here are invented. The reported real-world cases (Яготин, Полтава)
appear only as city names and coordinates that are not attached to any real
street address.
"""

import pytest

from app.services.list_checks import (
    DEFAULT_OUTLIER_KM,
    describe_outlier,
    flag_distance_outliers,
    haversine_km,
    infer_dominant_city,
)
from app.services.geocode_service import BatchGeocodeResponseItem


def row(
    text: str,
    *,
    status: str = "resolved",
    city: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
) -> BatchGeocodeResponseItem:
    return BatchGeocodeResponseItem(
        index=0,
        original=text,
        status=status,
        coordinate={"latitude": lat, "longitude": lon} if lat is not None else None,
        found_city=city,
    )


# --- dominant city ----------------------------------------------------------


def test_no_suggestion_below_five_confident_rows():
    rows = [row(f"addr {i}", city="Київ") for i in range(4)]
    assert infer_dominant_city(rows) is None


def test_five_confident_rows_in_one_city_are_enough():
    rows = [row(f"addr {i}", city="Київ") for i in range(5)]
    suggestion = infer_dominant_city(rows)
    assert suggestion is not None
    assert suggestion.city == "Київ"
    assert suggestion.resolved_count == 5
    assert suggestion.share == 1.0


def test_majority_city_is_suggested():
    rows = [row(f"addr {i}", city="Київ") for i in range(6)]
    rows.append(row("odd one", city="Полтава"))
    suggestion = infer_dominant_city(rows)
    assert suggestion is not None
    assert suggestion.city == "Київ"
    assert suggestion.total_confident == 7
    assert suggestion.share == pytest.approx(6 / 7)


def test_no_suggestion_without_a_clear_majority():
    rows = [row(f"addr {i}", city="Київ") for i in range(4)]
    rows += [row(f"other {i}", city="Львів") for i in range(4)]
    assert infer_dominant_city(rows) is None


def test_rows_that_are_not_resolved_do_not_count():
    rows = [row(f"addr {i}", city="Київ") for i in range(6)]
    rows.append(row("partial one", status="partial", city="Київ"))
    rows.append(row("ambiguous one", status="ambiguous", city="Київ"))
    suggestion = infer_dominant_city(rows)
    assert suggestion is not None
    assert suggestion.total_confident == 6


def test_rows_without_a_reported_city_do_not_count():
    rows = [row(f"addr {i}", city=None) for i in range(6)]
    assert infer_dominant_city(rows) is None


def test_a_single_outlier_cannot_become_the_suggestion():
    """A city needs 60% of confident rows, so one bad match cannot win."""
    rows = [row(f"addr {i}", city="Полтава") for i in range(2)]
    rows += [row(f"addr {i}", city="Київ") for i in range(8)]
    suggestion = infer_dominant_city(rows)
    assert suggestion is not None
    assert suggestion.city == "Київ"


def test_thresholds_are_configurable():
    """Two in Київ, one in Львів: a tie at 50%, no tie at 80%."""
    rows = [row("a", city="Київ"), row("b", city="Київ"), row("c", city="Львів")]
    assert infer_dominant_city(rows, min_count=2, min_share=0.5) is not None
    assert infer_dominant_city(rows, min_count=2, min_share=0.8) is None
    assert infer_dominant_city(rows, min_count=3, min_share=0.5) is None


def test_min_count_applies_to_the_city_not_the_list():
    """Four in Київ and one in Полтава is not a suggestion at min_count=5."""
    rows = [row(f"addr {i}", city="Київ") for i in range(4)]
    rows.append(row("odd", city="Полтава"))
    assert infer_dominant_city(rows) is None
    assert infer_dominant_city(rows, min_count=4) is not None


# --- distance outliers ------------------------------------------------------


def test_haversine_known_distance():
    # Kyiv to Poltava is roughly 300 km.
    distance = haversine_km(50.4501, 30.5234, 49.5887, 34.5113)
    assert 290 < distance < 310


def test_haversine_is_zero_for_the_same_point():
    assert haversine_km(50.45, 30.52, 50.45, 30.52) == pytest.approx(0.0)


def test_outlier_in_another_region_is_flagged():
    """The reported failure shape: one stop about 300 km from the rest."""
    rows = [
        row("stop 1", city="Київ", lat=50.4501, lon=30.5234),
        row("stop 2", city="Київ", lat=50.4590, lon=30.5100),
        row("stop 3", city="Київ", lat=50.4400, lon=30.5300),
        row("stop 4", city="Київ", lat=50.4650, lon=30.5400),
        row("far one", city="Полтава", lat=49.5887, lon=34.5113),
    ]
    flagged = flag_distance_outliers(rows)
    assert list(flagged) == ["far one"]
    # The distance comes back with the flag so the UI can report it.
    assert flagged["far one"] == pytest.approx(300.5, abs=1.0)


def test_tightly_clustered_stops_are_not_flagged():
    rows = [
        row("stop 1", lat=50.4501, lon=30.5234),
        row("stop 2", lat=50.4590, lon=30.5300),
        row("stop 3", lat=50.4400, lon=30.5100),
        row("stop 4", lat=50.4650, lon=30.5400),
    ]
    assert flag_distance_outliers(rows) == {}


def test_threshold_is_configurable():
    """The same rows are judged differently at different limits."""
    rows = [
        row("centre 1", lat=50.4501, lon=30.5234),
        row("centre 2", lat=50.4502, lon=30.5235),
        row("centre 3", lat=50.4500, lon=30.5233),
        # ~13 km from the cluster: fine at 50 km, flagged at 10 km.
        row("far", lat=50.55, lon=30.62),
    ]
    assert flag_distance_outliers(rows, max_km=50.0) == {}
    assert list(flag_distance_outliers(rows, max_km=10.0)) == ["far"]
    assert list(flag_distance_outliers(rows, max_km=5.0)) == ["far"]


def test_fewer_than_three_points_is_not_judged():
    """Two points have no middle, so neither can be called the odd one out."""
    rows = [
        row("only one", lat=50.4501, lon=30.5234),
        row("other", lat=49.5887, lon=34.5113),
    ]
    assert flag_distance_outliers(rows) == {}


def test_only_resolved_rows_with_coordinates_are_considered():
    rows = [
        row("stop 1", lat=50.4501, lon=30.5234),
        row("stop 2", lat=50.4590, lon=30.5300),
        row("stop 3", lat=50.4400, lon=30.5100),
        row("partial", status="partial", lat=49.5887, lon=34.5113),
        row("no coordinate"),
    ]
    assert flag_distance_outliers(rows) == {}


def test_outlier_does_not_drag_the_centre_towards_itself():
    """Distance is measured to the median of the others, so a far row stays far."""
    rows = [
        row("stop 1", lat=50.4501, lon=30.5234),
        row("stop 2", lat=50.4590, lon=30.5300),
        row("stop 3", lat=50.4400, lon=30.5100),
        row("stop 4", lat=50.4650, lon=30.5400),
        row("far one", lat=49.5887, lon=34.5113),
    ]
    flagged = flag_distance_outliers(rows)
    assert "far one" in flagged
    assert "stop 1" not in flagged


def test_default_threshold_is_fifty_km():
    assert DEFAULT_OUTLIER_KM == 50.0


def test_outlier_wording_is_plain():
    text = describe_outlier(184.7, 50.0)
    assert "185 km" in text
    assert "50 km" in text
    assert "geocod" not in text.lower()