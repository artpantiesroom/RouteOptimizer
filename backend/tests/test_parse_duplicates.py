from app.services.parse_service import ParseService


def test_duplicates_new_fields():
    svc = ParseService()
    # three identical with spacing/case variations
    res = svc.parse("Київ, Хрещатик 22\nкиїв,  хрещатик 22 \nКиїв, Хрещатик 22")
    items = [i for i in res.items if not i.is_blank]
    assert items[0].duplicate_of is None
    assert items[1].duplicate_of == 0
    assert items[1].is_duplicate
    assert items[2].duplicate_of == 0
    assert items[2].is_duplicate


def test_case_only():
    svc = ParseService()
    res = svc.parse("Moscow\nmoscow")
    items = [i for i in res.items if not i.is_blank]
    assert items[0].duplicate_of is None
    assert items[1].duplicate_of == 0


def test_whitespace_only():
    svc = ParseService()
    res = svc.parse("A\n  A  \nA")
    items = [i for i in res.items if not i.is_blank]
    assert items[0].duplicate_of is None
    assert items[1].duplicate_of == 0
    assert items[2].duplicate_of == 0


def test_apostrophe_normalized():
    svc = ParseService()
    res = svc.parse("O'Neill\nO’Neill\nOʼNeill")
    items = [i for i in res.items if not i.is_blank]
    assert items[0].duplicate_of is None
    assert items[1].duplicate_of == 0
    assert items[2].duplicate_of == 0
