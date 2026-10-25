import sqlite3
import threading
from types import SimpleNamespace

import pytest
from conftest import buyer_msg, supplier_msg

from parley import analyst, policy as pol_mod, runner, writer
from parley.dossier import Dossier
from parley.kit.client import ArenaError
from parley.kit.data import DataPackage
from parley.policy import CasePolicy

STD_T1 = {"volume_per_year": 4000, "ppm_commitment": 650, "chargeback_eur_per_complaint": 0.0, "tooling_eur": 0.0}
OPEN_T1 = {"unit_price": 52.9, "payment_days": 30, "contract_months": 12, "effective_date": "2026-12-01"}
RFQ_STD = {"volume_per_year": 6000, "incoterm": "EXW", "ppm_commitment": 1100}


@pytest.fixture(autouse=True)
def _adopted_only(monkeypatch):
    import os

    for k in list(os.environ):
        if k.startswith("PARLEY_"):
            monkeypatch.delenv(k, raising=False)


def policy(case):
    return CasePolicy(case, Dossier(case, None))


def final_threads(final, alternatives=(), body=None):
    ask = {"unit_price": 47.0, "payment_days": 30, "contract_months": 24, "effective_date": "2027-04-01"}
    kw = {"body": body} if body else {}
    return {
        "sup:T1": [
            supplier_msg("sup:T1", 0, OPEN_T1, standard=STD_T1),
            buyer_msg([ask]),
            supplier_msg("sup:T1", 1, final, status="final_offer", alternatives=alternatives, **kw),
        ]
    }


# ---- G1: no counter after a final offer ---------------------------------------------------------


@pytest.mark.parametrize("price", [47.5, 49.5, 52.0, 60.0])
@pytest.mark.parametrize(
    "body", [None, "This is our final offer. We cannot go lower; cash flow is tight.", "Final. Take it or leave it."]
)
def test_g1_final_offer_gets_no_counter_and_no_walk(case, price, body):
    final = {"unit_price": price, "payment_days": 30, "contract_months": 24, "effective_date": "2027-04-01"}
    d = policy(case).decide("sup:T1", final_threads(final, body=body))
    assert d.kind == "done" and not d.sheets


def test_g1_final_offer_only_mirrors_the_suppliers_own_sheet(case):
    final = {"unit_price": 49.9, "payment_days": 30, "contract_months": 24, "effective_date": "2027-04-01"}
    alt = {"unit_price": 49.4, "payment_days": 30, "contract_months": 24, "effective_date": "2027-04-01"}
    threads = final_threads(final, alternatives=[alt])
    pol = policy(case)
    d = pol.decide("sup:T1", threads)
    assert d.kind == "send" and d.sheets == [alt] and d.lines.get("mirror")
    threads["sup:T1"] += [buyer_msg([alt]), supplier_msg("sup:T1", 2, final, status="final_offer", alternatives=[alt])]
    d2 = pol.decide("sup:T1", threads)
    assert d2.kind == "done"


def test_g1_dearer_alternative_is_not_mirrored(case):
    final = {"unit_price": 49.4, "payment_days": 30, "contract_months": 24, "effective_date": "2027-04-01"}
    alt = {"unit_price": 51.0, "payment_days": 30, "contract_months": 24, "effective_date": "2027-04-01"}
    d = policy(case).decide("sup:T1", final_threads(final, alternatives=[alt]))
    assert d.kind == "done"


# ---- G2: RFQ take condition closes at once ------------------------------------------------------


def test_g2_take_gap_default_is_four_percent():
    assert pol_mod.RFQ_TAKE_GAP == 0.04


def test_g2_rfq_clear_leader_within_take_gap_is_closed(rfq_case):
    a0 = {"unit_price": 18.0, "payment_days": 30, "lead_time_days": 35, "incoterm": "EXW"}
    ask = {**a0, "unit_price": 15.0}
    threads = {
        "sup:A": [supplier_msg("sup:A", 0, a0, standard=RFQ_STD), buyer_msg([ask]),
                  supplier_msg("sup:A", 1, {**a0, "unit_price": 15.3})],
        "sup:B": [supplier_msg("sup:B", 0, {**a0, "unit_price": 19.0}, standard=RFQ_STD)],
    }
    d = policy(rfq_case).decide("sup:A", threads)
    assert d.kind == "done" and not d.sheets


def test_g2_rfq_gap_above_take_gap_keeps_negotiating(rfq_case):
    a0 = {"unit_price": 18.0, "payment_days": 30, "lead_time_days": 35, "incoterm": "EXW"}
    ask = {**a0, "unit_price": 15.0}
    threads = {
        "sup:A": [supplier_msg("sup:A", 0, a0, standard=RFQ_STD), buyer_msg([ask]),
                  supplier_msg("sup:A", 1, {**a0, "unit_price": 16.5})],
        "sup:B": [supplier_msg("sup:B", 0, {**a0, "unit_price": 19.0}, standard=RFQ_STD)],
    }
    d = policy(rfq_case).decide("sup:A", threads)
    assert "clearly the best" not in d.reason


# ---- G3: acknowledge only the stated concern ----------------------------------------------------


def test_g3_ack_opening_is_adopted():
    assert writer.ADOPTED.get("ACK_OPENING") == "1"


def test_g3_cost_drivers_are_never_acknowledged(case):
    body = "Energy, raw material and wage costs have risen sharply; these cost drivers force the increase."
    r = analyst.read_reply({"body": body})
    ack = writer.acknowledgement(case, r, {"body": body})
    assert ack == ""


def test_g3_ack_names_only_cash_or_margin(case):
    body = "Our liquidity is tight and the pressure on our margin is high; energy costs rose."
    r = analyst.read_reply({"body": body})
    ack = writer.acknowledgement(case, r, {"body": body})
    assert "faster payment" in ack and "margin" in ack
    assert "energy" not in ack.lower() and "cost" not in ack.lower()


# ---- G4: index clause guards --------------------------------------------------------------------


def test_g4_hedge_noclause_adopted_and_margin_constant():
    assert pol_mod.ADOPTED.get("PARLEY_HEDGE_NOCLAUSE") == "1"
    assert pol_mod.flag("PARLEY_HEDGE_NOCLAUSE")
    assert pol_mod.CLAUSE_MARGIN == 1.2


# ---- G6: cash need only from the supplier's own words --------------------------------------------


def test_g6_cash_strict_is_adopted():
    assert pol_mod.flag("PARLEY_CASH_STRICT")


@pytest.mark.parametrize(
    "body", ["We would welcome faster payment.", "Payment terms are as usual.", "Thank you for your order."]
)
def test_g6_stock_phrases_are_no_cash_need(body):
    assert not analyst.read_reply({"body": body}).flags.get("cash")


@pytest.mark.parametrize(
    "body", ["Our liquidity is tight at the moment.", "Our bank has cut the credit line.", "We offer 2% skonto."]
)
def test_g6_stated_cash_need_is_read(body):
    assert analyst.read_reply({"body": body}).flags.get("cash")


# ---- G7: idempotent sends -----------------------------------------------------------------------


class FakeClient:
    def __init__(self, thread=None, session=None):
        self.calls = []
        self._thread = thread or {"messages": [], "closed": None}
        self._session = session or {"status": "open"}

    def session(self, *a, **k):
        self.calls.append("session")
        return self._session

    def thread(self, *a, **k):
        self.calls.append("thread")
        return self._thread


def bare_runner(client):
    r = runner.CaseRunner.__new__(runner.CaseRunner)
    r.client, r.session_id, r.deadline = client, "s1", None
    r.lock, r.closing = threading.Lock(), threading.Event()
    r.threads, r.done = {"sup:A": []}, set()
    r.journal = SimpleNamespace(log=lambda *a, **k: None)
    r.policy = SimpleNamespace(replied_at={})
    r.thread_events = lambda t, **k: list(t.get("messages") or [])
    return r


def test_g7_send_timeout_is_at_least_180_s():
    assert runner.SEND_TIMEOUT_S >= 180


def test_g7_409_rereads_session_and_thread_before_any_resend():
    c = FakeClient()
    r = bare_runner(c)
    assert r.after_409("sup:A", ArenaError(409, "busy")) is False
    assert c.calls == ["session", "thread"]


def test_g7_409_on_closed_thread_ends_the_thread():
    c = FakeClient(thread={"messages": [], "closed": "agreed"})
    r = bare_runner(c)
    assert r.after_409("sup:A", ArenaError(409, "busy")) is True and "sup:A" in r.done


def test_g7_transient_send_error_rereads_thread_first(monkeypatch):
    c = FakeClient()
    r = bare_runner(c)
    monkeypatch.setattr(r.closing, "wait", lambda *a: False)
    assert r.recover_send("sup:A", ArenaError(503, "down")) is True
    assert c.calls == ["thread"]


def test_g7_non_transient_error_is_not_retried():
    c = FakeClient()
    r = bare_runner(c)
    assert r.recover_send("sup:A", ArenaError(400, "bad")) is False and c.calls == []


# ---- G8: first ask within the tested depth ------------------------------------------------------


@pytest.mark.parametrize("goal", ["leverage", "routine", "quality_first", "stabilise", "secure_supply"])
def test_g8_anchor_never_deeper_than_max_depth(case, goal):
    case["buyer"]["category_goal"] = goal
    case["buyer"]["current_unit_price"] = 30.0
    p = policy(case)
    threads = {"sup:T1": [supplier_msg("sup:T1", 0, OPEN_T1, standard=STD_T1)]}
    v = p.view("sup:T1", threads["sup:T1"])
    assert p.anchor_price("sup:T1", v) >= OPEN_T1["unit_price"] * (1 - pol_mod.MAX_DEPTH) - 1e-9


def test_g8_rfq_anchor_never_deeper_than_rfq_max_depth(rfq_case):
    a0 = {"unit_price": 18.0, "payment_days": 30, "lead_time_days": 35, "incoterm": "EXW"}
    p = policy(rfq_case)
    v = p.view("sup:A", [supplier_msg("sup:A", 0, a0, standard=RFQ_STD)])
    assert p.anchor_price("sup:A", v) >= 18.0 * (1 - pol_mod.RFQ_MAX_DEPTH) - 1e-9


# ---- G9: a dossier error still plays with a plain profile ---------------------------------------


class BrokenPackage:
    facts, release, db = {}, None, object()

    def query(self, *a, **k):
        raise sqlite3.ProgrammingError("Recursive use of cursors not allowed.")

    def lookup(self, ref):
        return None


def test_g9_data_error_gives_a_plain_profile_and_still_plays(case):
    d = Dossier(case, BrokenPackage())
    assert "sup:T1" in d.profiles
    dec = CasePolicy(case, d).decide("sup:T1", {"sup:T1": [supplier_msg("sup:T1", 0, OPEN_T1, standard=STD_T1)]})
    assert dec.kind == "send" and dec.sheets


def test_g9_shared_connection_survives_parallel_queries(tmp_path):
    db = tmp_path / "p.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t (id TEXT, v INTEGER)")
    con.executemany("INSERT INTO t VALUES (?, ?)", [(f"x:{i}", i) for i in range(500)])
    con.commit()
    con.close()
    pkg = DataPackage(tmp_path)
    errors = []

    def work():
        try:
            for _ in range(50):
                assert len(pkg.query("SELECT * FROM t", limit=500)) == 500
                pkg.lookup("x:7")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=work) for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors
