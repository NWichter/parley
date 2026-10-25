import copy
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

import pytest
from conftest import BASE_CASE

from parley.dossier import Dossier, load_package

# ---- 1. dossier queries ------------------------------------------------------------------------


def test_failing_query_is_logged_not_silent(case, caplog):
    class Pkg:
        db, facts, release = object(), {}, None

        def query(self, *a, **k):
            raise RuntimeError("boom")

        def lookup(self, ref):
            return None

    with caplog.at_level(logging.WARNING, logger="parley"):
        d = Dossier({**case, "suppliers": ["sup:T1"]}, Pkg())
    assert "sup:T1" in d.profiles
    assert any("query failed" in r.getMessage() and "boom" in r.getMessage() for r in caplog.records)


def test_master_terms_are_read_once_per_supplier(case):
    calls = []

    class Pkg:
        db, facts, release = object(), {}, None

        def query(self, sql, params=(), limit=0):
            calls.append(sql)
            if "standard_lead_time_days" in sql:
                return [{"payment_days": 30, "incoterm": "FCA", "standard_lead_time_days": 35}]
            return []

        def lookup(self, ref):
            return None

    d = Dossier({**case, "suppliers": ["sup:T1"]}, Pkg())
    first = d.master_terms("sup:T1")
    first["payment_days"] = 99
    for _ in range(5):
        assert d.master_terms("sup:T1") == {"payment_days": 30, "incoterm": "FCA", "lead_time_days": 35}
    assert sum("standard_lead_time_days" in s for s in calls) == 1


def _full_detail_cases(pkg):
    by_sid = {}
    for c in pkg.cases.values():
        for s in c.get("suppliers", []):
            by_sid.setdefault(s, c)
    out = []
    for row in pkg.query("SELECT supplier_id FROM supplier_intel", limit=1000):
        sid = row["supplier_id"]
        base = copy.deepcopy(by_sid.get(sid) or BASE_CASE)
        base["suppliers"] = [sid]
        out.append(base)
    return out


def _snapshot(case, pkg):
    d = Dossier(case, pkg)
    sid = case["suppliers"][0]
    return sid, asdict(d.profiles[sid]), [asdict(c) for c in d.claims(sid)], d.master_terms(sid)


def test_dossiers_are_identical_with_one_or_ten_threads():
    pkg = load_package()
    if pkg is None or pkg.db is None:
        pytest.skip("no data package")
    cases = _full_detail_cases(pkg)
    assert len(cases) >= 10
    serial = [_snapshot(c, pkg) for c in cases]
    with ThreadPoolExecutor(max_workers=10) as ex:
        parallel = list(ex.map(lambda c: _snapshot(c, pkg), cases * 2))
    assert parallel[: len(cases)] == serial
    assert parallel[len(cases) :] == serial
    assert any(p[1]["cash_short"] or p[1]["capacity_free"] or p[1]["insolvent"] for p in serial)
    assert any(p[2] for p in serial)


# ---- 2. a failing decision in round 0 ----------------------------------------------------------


def _crash_run(case, monkeypatch, flag):
    import time

    from conftest import supplier_msg
    from test_robust import OPEN, STD, FakeClient

    from parley.policy import CasePolicy
    from parley.runner import CaseRunner

    if flag:
        monkeypatch.setenv("PARLEY_SAFE_CLOSE", "1")
    else:
        monkeypatch.delenv("PARLEY_SAFE_CLOSE", raising=False)
    calls = []

    def boom(self, sid, threads):
        calls.append(sid)
        raise RuntimeError("boom")

    monkeypatch.setattr(CasePolicy, "decide", boom)
    threads = {"sup:T1": [supplier_msg("sup:T1", 0, OPEN, standard=STD)]}
    client = FakeClient(case, threads)
    cap = time.time() + 3600
    CaseRunner(client, case["case_id"], None, case=case, deadline=lambda: cap).play()
    sends = [c[2] for c in client.calls if c[0] == "send"]
    return sends, calls


def test_without_the_flag_a_crash_in_round_0_ends_the_case(case, monkeypatch):
    sends, _ = _crash_run(case, monkeypatch, flag=False)
    assert sends[0].get("action", {}).get("type") in ("accept", "walk_away")


def test_safe_close_retries_then_sends_the_anchor_before_any_close(case, monkeypatch):
    sends, calls = _crash_run(case, monkeypatch, flag=True)
    assert "action" not in sends[0] and sends[0]["term_sheets"]
    assert sends[0]["term_sheets"][0]["terms"]["unit_price"] < 52.9
    assert calls[:2] == ["sup:T1", "sup:T1"]


def test_close_holds_in_round_0_only_with_time_left_and_no_final(case, monkeypatch):
    from conftest import supplier_msg
    from test_engine import OPEN_T1, STD_T1, policy

    monkeypatch.setenv("PARLEY_SAFE_CLOSE", "1")
    pol = policy(case)
    threads = {"sup:T1": [supplier_msg("sup:T1", 0, OPEN_T1, standard=STD_T1)]}
    assert pol.close(threads, "t", time_left=True).kind == "hold"
    assert pol.close(threads, "t", time_left=False).kind != "hold"
    final = {"sup:T1": [supplier_msg("sup:T1", 0, OPEN_T1, status="final_offer", standard=STD_T1)]}
    assert pol.close(final, "t", time_left=True).kind != "hold"
    monkeypatch.delenv("PARLEY_SAFE_CLOSE")
    assert pol.close(threads, "t", time_left=True).kind != "hold"


def test_a_round_0_done_holds_and_sends_the_anchor(case, monkeypatch):
    import time

    from conftest import supplier_msg
    from test_robust import OPEN, STD, FakeClient

    from parley.policy import CasePolicy, Decision
    from parley.runner import CaseRunner

    monkeypatch.setenv("PARLEY_SAFE_CLOSE", "1")
    monkeypatch.setattr(CasePolicy, "decide", lambda self, sid, th: Decision("done", sid, reason="no valid package"))
    client = FakeClient(case, {"sup:T1": [supplier_msg("sup:T1", 0, OPEN, standard=STD)]})
    cap = time.time() + 3600
    CaseRunner(client, case["case_id"], None, case=case, deadline=lambda: cap).play()
    sends = [c[2] for c in client.calls if c[0] == "send"]
    assert "action" not in sends[0] and sends[0]["term_sheets"]
    assert sends[1]["action"]["type"] == "accept"


# ---- 3. walk check on a U-shaped cost (relationship goal) --------------------------------------


def _relationship(case, prices):
    from conftest import buyer_msg, supplier_msg
    from test_engine import OPEN_T1, STD_T1, policy

    case["buyer"].update(category_goal="stabilise", sustainable_margin_pct=8, relationship_eur_per_point=20000)
    case["buyer"]["alternative"] = {"kind": "quote", "terms": {"unit_price": 53.8, "payment_days": 30}}
    pol = policy(case)
    pol.dossier.profiles["sup:T1"].full_cost = 46.0
    threads = {"sup:T1": [supplier_msg("sup:T1", 0, OPEN_T1, standard=STD_T1)]}
    for n, p in enumerate(prices, start=1):
        threads["sup:T1"] += [buyer_msg([{**OPEN_T1, "unit_price": 45.0}]),
                              supplier_msg("sup:T1", n, {**OPEN_T1, "unit_price": p}, standard=STD_T1)]
    return pol, threads


def test_walk_check_uses_the_cost_minimum_not_the_floor(case, monkeypatch):
    monkeypatch.delenv("PARLEY_WALK_CMIN", raising=False)
    pol, threads = _relationship(copy.deepcopy(case), (52.0, 51.0))
    d = pol.decide("sup:T1", threads)
    v = pol.view("sup:T1", threads["sup:T1"])
    assert v.c_standing > pol.val.c_alt
    assert d.kind == "walk"
    monkeypatch.setenv("PARLEY_WALK_CMIN", "1")
    pol, threads = _relationship(copy.deepcopy(case), (52.0, 51.0))
    d = pol.decide("sup:T1", threads)
    m, v = pol.models["sup:T1"], pol.view("sup:T1", threads["sup:T1"])
    p_min = pol.val.cheapest_price(pol.val.combo_of(v.terms), v.std, m, 500)
    assert pol.cost("sup:T1", {**v.terms, "unit_price": p_min}, v.std) < pol.val.c_alt
    assert d.kind == "send"


def test_a_counter_at_the_cost_minimum_closes_at_once(case, monkeypatch):
    monkeypatch.delenv("PARLEY_WALK_CMIN", raising=False)
    pol, threads = _relationship(copy.deepcopy(case), (52.0, 49.5))
    assert pol.decide("sup:T1", threads).kind == "send"
    monkeypatch.setenv("PARLEY_WALK_CMIN", "1")
    pol, threads = _relationship(copy.deepcopy(case), (52.0, 49.5))
    d = pol.decide("sup:T1", threads)
    assert d.kind == "done" and "cost minimum" in d.reason
    assert pol.close(threads, "t").kind == "accept"


def test_walk_cmin_changes_nothing_without_a_relationship_goal(case, monkeypatch):
    from conftest import buyer_msg, supplier_msg
    from test_engine import OPEN_T1, STD_T1, policy

    threads = {"sup:T1": [supplier_msg("sup:T1", 0, OPEN_T1, standard=STD_T1)]}
    for n, p in enumerate((52.0, 49.5), start=1):
        threads["sup:T1"] += [buyer_msg([{**OPEN_T1, "unit_price": 45.0}]),
                              supplier_msg("sup:T1", n, {**OPEN_T1, "unit_price": p}, standard=STD_T1)]
    monkeypatch.delenv("PARLEY_WALK_CMIN", raising=False)
    off = policy(copy.deepcopy(case)).decide("sup:T1", threads)
    monkeypatch.setenv("PARLEY_WALK_CMIN", "1")
    on = policy(copy.deepcopy(case)).decide("sup:T1", threads)
    assert (off.kind, off.sheets, off.reason) == (on.kind, on.sheets, on.reason)
