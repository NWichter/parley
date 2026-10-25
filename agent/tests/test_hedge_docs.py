import pytest

from parley.dossier import Dossier

LETTER = (
    "for your planning: we have secured our steel sheet and coil for 2027, bought forward at fixed prices "
    "with our mill. Whatever the steel market does reaches our prices only from 2028."
)


class Pkg:
    db, facts, release = object(), {}, None

    def query(self, sql, params=(), limit=0):
        return []

    def lookup(self, ref):
        return None


def hedged(case, text):
    facts = {
        f"idx:{s}@2026-0{m}": {"series": s, "month": f"2026-0{m}", "value": 100.0 + m}
        for s in ("PPI-C241", "CU-NOTATION")
        for m in (1, 2)
    }
    d = Dossier({**case, "suppliers": ["sup:T1"], "facts": facts}, Pkg())
    p = d.profiles["sup:T1"]
    p.hedged_series.clear()
    d._read_text(p, text)
    return p.hedged_series


@pytest.mark.parametrize("value,expect", [(None, ["PPI-C241"]), ("0", [])])
def test_cover_named_by_year_is_a_hedge(case, monkeypatch, value, expect):
    if value is None:
        monkeypatch.delenv("PARLEY_HEDGE_DOCS", raising=False)
    else:
        monkeypatch.setenv("PARLEY_HEDGE_DOCS", value)
    assert hedged(case, LETTER) == expect


def test_cover_until_still_counts_and_is_ordered(case):
    assert hedged(case, "our copper requirement is covered at fixed prices until March 2027.") == ["CU-NOTATION"]
    assert hedged(case, "we secured a large order for 2027 and run three shifts.") == []


def test_secured_needs_cover_wording_in_the_same_sentence(case, monkeypatch):
    monkeypatch.delenv("PARLEY_HEDGE_DOCS", raising=False)
    assert hedged(case, "We have secured additional steel capacity for 2027 to serve your growth.") == []
