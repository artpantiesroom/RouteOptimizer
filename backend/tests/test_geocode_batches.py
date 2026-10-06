import pytest

from app.services.geocode_service import (
    BatchGeocodeRequestItem,
    BatchGeocodeResponseItem,
    geocode_in_batches,
)


class FakeService:
    """Records every geocode_batch call and answers with items in input order."""

    def __init__(self, cities=None, coords=None):
        self.calls = []
        self.cities = []
        self.resolved = True
        self.cities_by_original = cities or {}
        self.coords_by_original = coords or {}

    async def geocode_batch(self, items, city=None):
        self.calls.append(list(items))
        self.cities.append(city)
        results = []
        for item in items:
            if not self.resolved:
                results.append(
                    BatchGeocodeResponseItem(
                        index=item.index, original=item.original, status="not_found"
                    )
                )
                continue
            coordinate = self.coords_by_original.get(
                item.original, {"latitude": 50.45 + item.index, "longitude": 30.5 + item.index}
            )
            results.append(
                BatchGeocodeResponseItem(
                    index=item.index,
                    original=item.original,
                    status="resolved",
                    coordinate=coordinate,
                    found_city=self.cities_by_original.get(item.original),
                )
            )
        return results


def item(text: str, index: int, trimmed: str | None = None) -> BatchGeocodeRequestItem:
    return BatchGeocodeRequestItem(index=index, original=text, trimmed=trimmed or text)


def make_items(texts):
    return [item(text, i) for i, text in enumerate(texts)]


@pytest.mark.asyncio
async def test_chunks_by_five_and_returns_batches_in_input_order():
    service = FakeService()
    texts = [f"addr {i}" for i in range(12)]
    results = await geocode_in_batches(service, make_items(texts), city="Київ")

    assert len(service.calls) == 3
    assert [len(c) for c in service.calls] == [5, 5, 2]
    assert [[i.index for i in c] for c in service.calls] == [
        list(range(5)),
        list(range(5, 10)),
        list(range(10, 12)),
    ]
    assert service.cities == ["Київ", "Київ", "Київ"]
    assert [r.index for r in results] == list(range(12))
    assert [r.original for r in results] == texts


@pytest.mark.asyncio
async def test_empty_list_makes_no_calls():
    service = FakeService()
    results = await geocode_in_batches(service, [])
    assert service.calls == []
    assert results == []


@pytest.mark.asyncio
async def test_small_lists_within_one_batch():
    for n in (1, 5):
        service = FakeService()
        results = await geocode_in_batches(service, make_items([f"a{i}" for i in range(n)]))
        assert len(service.calls) == 1
        assert len(results) == n


@pytest.mark.asyncio
async def test_exactly_six_needs_two_batches():
    service = FakeService()
    results = await geocode_in_batches(service, make_items([f"a{i}" for i in range(6)]))
    assert len(service.calls) == 2
    assert len(results) == 6
    assert [r.index for r in results] == [0, 1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_twenty_items_are_split_into_four_batches():
    service = FakeService()
    results = await geocode_in_batches(service, make_items([f"a{i}" for i in range(20)]))
    assert [len(c) for c in service.calls] == [5, 5, 5, 5]
    assert len(results) == 20
    assert [r.index for r in results] == list(range(20))


@pytest.mark.asyncio
async def test_duplicates_are_forwarded_untouched_the_service_dedupes():
    # The helper must not dedupe: identical lines are fine where the address
    # really does repeat, and geocode_batch dedupes only inside one chunk.
    service = FakeService()
    texts = ["a", "a", "b", "a", "b", "c"]
    results = await geocode_in_batches(service, make_items(texts))
    assert len(service.calls) == 2
    assert [len(c) for c in service.calls] == [5, 1]
    # The second batch receives the duplicate "c" alone.
    assert [i.original for i in service.calls[0]] == ["a", "a", "b", "a", "b"]
    assert [i.original for i in service.calls[1]] == ["c"]
    assert [r.original for r in results] == texts
    assert [r.index for r in results] == [0, 1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_original_indexes_are_preserved():
    service = FakeService()
    texts = [f"a{i}" for i in range(6)]
    items = [BatchGeocodeRequestItem(index=i * 10, original=t, trimmed=t) for i, t in enumerate(texts)]
    results = await geocode_in_batches(service, items)
    assert [r.index for r in results] == [0, 10, 20, 30, 40, 50]


@pytest.mark.asyncio
async def test_on_batch_sees_each_chunk_and_its_results():
    service = FakeService()
    seen = []

    def on_batch(start, chunk, batch):
        seen.append((start, [i.original for i in chunk], [r.index for r in batch]))

    texts = [f"a{i}" for i in range(7)]
    await geocode_in_batches(service, make_items(texts), on_batch=on_batch)

    assert seen == [
        (0, texts[:5], [0, 1, 2, 3, 4]),
        (5, texts[5:], [5, 6]),
    ]


@pytest.mark.asyncio
async def test_batch_size_must_be_positive():
    service = FakeService()
    with pytest.raises(ValueError):
        await geocode_in_batches(service, make_items(["a"]), batch_size=0)
    assert service.calls == []


# --- whole-list checks across chunk boundaries -------------------------------


@pytest.mark.asyncio
async def test_flags_a_wrong_city_row_hiding_in_its_own_chunk():
    # Six К-row addresses fill the first chunk; the out-of-city row lands alone
    # in the second, where no dominant city and not enough points to compare
    # exist for the per-chunk checks to notice.
    kyiv = {"latitude": 50.45, "longitude": 30.5}
    poltava = {"latitude": 49.59, "longitude": 34.55}
    texts = [f"kirivo {i}" for i in range(6)] + ["poltava row"]
    cities = {t: "Київ" for t in texts}
    cities["poltava row"] = "Полтава"
    coords = {t: kyiv for t in texts}
    coords["poltava row"] = poltava

    service = FakeService(cities=cities, coords=coords)
    results = await geocode_in_batches(service, make_items(texts))

    by_original = {r.original: r for r in results}
    assert by_original["poltava row"].needs_check is True
    assert by_original["poltava row"].needs_check_reason == "Found in Полтава, outside Київ."
    assert by_original["poltava row"].retry_city == "Київ"
    # The rest are untouched.
    assert not any(r.needs_check for r in results if r.original != "poltava row")


@pytest.mark.asyncio
async def test_flags_a_far_row_that_slipped_past_its_own_chunk():
    # No city scope at all: the far row is still caught by whole-list distance.
    kyiv = {"latitude": 50.45, "longitude": 30.5}
    poltava = {"latitude": 49.59, "longitude": 34.55}
    texts = [f"a{i}" for i in range(6)] + ["far row"]
    coords = {t: kyiv for t in texts}
    coords["far row"] = poltava

    service = FakeService(coords=coords)
    results = await geocode_in_batches(service, make_items(texts))

    by_original = {r.original: r for r in results}
    assert by_original["far row"].needs_check is True
    assert "km from the other stops" in by_original["far row"].needs_check_reason
    assert not any(r.needs_check for r in results if r.original != "far row")


@pytest.mark.asyncio
async def test_whole_list_pass_never_removes_or_overwrites_a_flag():
    kyiv = {"latitude": 50.45, "longitude": 30.5}
    poltava = {"latitude": 49.59, "longitude": 34.55}

    class FlaggingService(FakeService):
        def __init__(self):
            super().__init__()
            self.calls = []

        async def geocode_batch(self, items, city=None):
            batch = await super().geocode_batch(items, city=city)
            # Simulate the per-chunk check having already flagged the row.
            for r in batch:
                if r.original == "far row":
                    r.needs_check = True
                    r.needs_check_reason = "Flagged by an earlier check."
            return batch

    texts = [f"a{i}" for i in range(6)] + ["far row"]
    coords = {t: kyiv for t in texts}
    coords["far row"] = poltava

    service = FlaggingService()
    service.coords_by_original = coords
    service.resolved = True
    results = await geocode_in_batches(service, make_items(texts))

    by_original = {r.original: r for r in results}
    assert by_original["far row"].needs_check is True
    assert by_original["far row"].needs_check_reason == "Flagged by an earlier check."


@pytest.mark.asyncio
async def test_single_chunk_keeps_the_scope_from_the_explicit_city():
    # With a city given, the whole-list pass flags rows outside it even in a
    # single chunk, matching the per-chunk behaviour.
    kyiv = {"latitude": 50.45, "longitude": 30.5}
    poltava = {"latitude": 49.59, "longitude": 34.55}
    texts = ["k1", "k2", "k3", "far row"]
    cities = {"k1": "Київ", "k2": "Київ", "k3": "Київ", "far row": "Полтава"}
    coords = {"k1": kyiv, "k2": kyiv, "k3": kyiv, "far row": poltava}

    service = FakeService(cities=cities, coords=coords)
    results = await geocode_in_batches(service, make_items(texts), city="Київ")

    by_original = {r.original: r for r in results}
    assert by_original["far row"].needs_check is True
    assert by_original["far row"].needs_check_reason == "Found in Полтава, outside Київ."