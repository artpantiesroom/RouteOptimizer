from app.services.parse_service import ParseService


def test_parse_basic():
    svc = ParseService()
    res = svc.parse("A\nB\nC")
    assert res.total == 3
    assert res.non_blank == 3
    assert all(not i.is_blank for i in res.items)
    assert [i.trimmed for i in res.items] == ["A", "B", "C"]


def test_parse_ignores_blanks():
    svc = ParseService()
    res = svc.parse("A\n\nB  \n  ")
    assert res.total == 4
    assert res.non_blank == 2
    assert res.items[1].is_blank
    assert res.items[3].is_blank


def test_parse_detects_duplicates_not_removed():
    svc = ParseService()
    res = svc.parse("Moscow\nmoscow\n  Moscow  ")
    assert res.total == 3
    assert res.non_blank == 3
    # all duplicates of first
    assert res.items[0].duplicate_of is None
    assert res.items[1].duplicate_of == 0
    assert res.items[2].duplicate_of == 0


def test_parse_preserves_order():
    svc = ParseService()
    res = svc.parse("B\nA\nB")
    assert [i.trimmed for i in res.items] == ["B", "A", "B"]
    assert res.items[0].duplicate_of is None
    assert res.items[1].duplicate_of is None
    assert res.items[2].duplicate_of == 0
