"""The Sunday release reaches the dossier: tested on a public sample release (same format).

Needs the package (ARENA_DATA or the usual places) and the release, unpacked to
agent/data/release-sample/release-sunday or named by RELEASE_TEST."""

import json
import os
from pathlib import Path

import pytest

from parley.dossier import Dossier, default_data_dir
from parley.kit.data import DataPackage
from parley.prepare import data_version
from parley.runner import load_personas

HERE = Path(__file__).resolve().parents[1]
RELEASE = Path(os.environ.get("RELEASE_TEST") or HERE / "data" / "release-sample" / "release-sunday")


@pytest.fixture(scope="module")
def pkgs():
    root = default_data_dir()
    if not root or not (RELEASE / "facts.json").is_file():
        pytest.skip("no data package or no release")
    base = DataPackage(root, release=HERE / "no-release")
    rel = DataPackage(root, release=RELEASE)
    assert base.release is None and rel.release is not None
    return base, rel


def suppliers(pkg):
    return [r["id"] for r in pkg.query("SELECT id FROM suppliers WHERE detail = 'full' ORDER BY id")]


def case(pkg, sid, refs=(), alternative=None):
    parts = [r["part_id"] for r in pkg.query(
        "SELECT part_id FROM sourcing WHERE supplier_id = ? AND status = 'active'", (sid,))][:2]
    buyer = {"alternative": alternative} if alternative else {}
    return {"case_id": "case:T", "type": "price_increase", "suppliers": [sid], "parts": parts,
            "refs": list(refs), "buyer": buyer}


def dossier(pkg, sid, **kw):
    return Dossier(case(pkg, sid, **kw), pkg)


def release_facts(pkg):
    return json.loads((pkg.release / "facts.json").read_text(encoding="utf-8"))


def test_memo_and_market_news_reach_matching_suppliers(pkgs):
    _, rel = pkgs
    facts = release_facts(rel)
    memo = next(k for k, f in facts.items() if f.get("type") == "memo" and not f.get("supplier"))
    for sid in suppliers(rel):
        p = dossier(rel, sid).profiles[sid]
        assert memo in p.market
        assert memo in {n["id"] for n in p.notes}
    market = "news:20260912-30"
    reached = [s for s in suppliers(rel) if market in dossier(rel, s).profiles[s].market]
    assert reached
    for sid in reached:
        drivers = dossier(rel, sid).profiles[sid].drivers
        assert any(s.startswith(("PPI-D35", "SMARD")) for s in drivers)


def test_supply_normalises_clears_capacity_tight(pkgs):
    base, rel = pkgs
    sid = "sup:70540"
    assert dossier(base, sid).profiles[sid].capacity_tight
    d = dossier(rel, sid)
    assert not d.profiles[sid].capacity_tight
    assert "news:20261008-S4" in {r for c in d.claims(sid) for r in c.refs}


def test_insolvency_sets_cash_and_risk_flag(pkgs):
    base, rel = pkgs
    sid = "sup:70140"
    assert not dossier(base, sid).profiles[sid].insolvent
    p = dossier(rel, sid).profiles[sid]
    assert p.insolvent and p.cash_short and p.interest_rate >= 0.14
    alt = {"kind": "quote", "ref": "quote:7200001", "terms": {"unit_price": 100.0}}
    other = "sup:70110"
    with_base = dossier(base, other, alternative=alt).claims(other)
    with_rel = dossier(rel, other, alternative=alt).claims(other)
    assert any(c.kind == "alt_quote" for c in with_base)
    assert not any(c.kind == "alt_quote" for c in with_rel)


def test_release_letters_set_flags(pkgs):
    _, rel = pkgs
    assert "PPI-C2431" in dossier(rel, "sup:70720").profiles["sup:70720"].hedged_series
    assert dossier(rel, "sup:70650").profiles["sup:70650"].price_freeze


def test_release_facts_become_citable_claims(pkgs):
    _, rel = pkgs
    facts = release_facts(rel)
    cited = set()
    for sid in suppliers(rel):
        own = [k for k, f in facts.items() if f.get("supplier") == sid]
        d = dossier(rel, sid, refs=own)
        for c in d.claims(sid):
            for r in c.refs:
                assert d.known(r), r
                assert d.citable(r), r
                if r in facts:
                    cited.add(r)
                    assert r in c.sentence
    assert {"news:20261008-S4", "news:20261002-S3"} <= cited
    assert not {k for k in cited if facts[k].get("topic") in ("insolvency", "noise")}


def test_all_suppliers_build_and_decisions_change(pkgs):
    base, rel = pkgs
    keys = ("cash_short", "capacity_tight", "capacity_free", "hedged_series", "insolvent", "interest_rate")
    facts = release_facts(rel)
    changed = 0
    for sid in suppliers(rel):
        own = [k for k, f in facts.items() if f.get("supplier") == sid]
        a, b = dossier(base, sid), dossier(rel, sid, refs=own)
        pa, pb = a.profiles[sid], b.profiles[sid]
        ca = [(c.kind, c.refs) for c in a.claims(sid)]
        cb = [(c.kind, c.refs) for c in b.claims(sid)]
        changed += any(getattr(pa, k) != getattr(pb, k) for k in keys) or ca != cb
    assert changed >= 5


def test_persona_cache_invalidates_on_data_version(pkgs, tmp_path):
    base, rel = pkgs
    cache = tmp_path / "personas.json"
    cache.write_text(json.dumps({
        "_meta": {"data": data_version(base)},
        "sup:70540": {"summary": "capacity is tight"},
        "sup:70110": {"summary": "family foundry"},
    }), encoding="utf-8")
    assert set(load_personas(base, cache)) == {"sup:70540", "sup:70110"}
    assert data_version(rel) != data_version(base)
    assert set(load_personas(rel, cache)) == {"sup:70110"}
