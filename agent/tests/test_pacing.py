import threading
import types

import pytest
from conftest import buyer_msg, supplier_msg

from parley import pacing, runner
from parley.dossier import Dossier
from parley.pacing import Pace
from parley.policy import CasePolicy

STD = {"volume_per_year": 4000, "ppm_commitment": 650, "chargeback_eur_per_complaint": 0.0, "tooling_eur": 0.0}
OPEN = {"unit_price": 52.9, "payment_days": 30, "contract_months": 12, "effective_date": "2026-12-01"}


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t, self.lock = t, threading.Lock()

    def time(self):
        return self.t

    def monotonic(self):
        return self.t

    def advance(self, s):
        with self.lock:
            self.t += s

    def sleep(self, _s):
        threading.Event().wait(0.0005)


def test_no_cap_means_no_pacing():
    assert Pace(shared=None).rounds("sup:T1", None) is None


def test_rounds_use_p90_for_the_next_and_the_mean_for_later_ones():
    clock = Clock()
    p = Pace(shared=None, clock=clock.time)
    assert p.estimate("sup:T1") == (pacing.PRIOR_RTT_S, pacing.PRIOR_P90_S)
    for x in (30, 34, 36, 80):
        p.add("sup:T1", x)
    e, p90 = p.estimate("sup:T1")
    assert 30 < e < 80 and p90 >= 80
    assert p.rounds("sup:T1", clock.t + p90 - 1) == 0
    assert p.rounds("sup:T1", clock.t + p90 + 2 * e + 1) == 3


def test_a_new_thread_borrows_the_other_threads_latency():
    shared = pacing.Pool()
    p = Pace(shared=shared)
    for x in (70, 75, 80, 85):
        p.add("sup:A", x)
    e, p90 = p.estimate("sup:B")
    assert e > 70 and p90 >= 85


def _round2(ask_price, reply_price):
    ask = {"unit_price": ask_price, "payment_days": 30, "contract_months": 24, "effective_date": "2027-04-01"}
    reply = {**ask, "unit_price": reply_price}
    return {
        "sup:T1": [supplier_msg("sup:T1", 0, OPEN, standard=STD), buyer_msg([ask]), supplier_msg("sup:T1", 1, reply)]
    }


def _policy(case, rounds):
    pol = CasePolicy(case, Dossier(case, None))
    pol.time_rounds["sup:T1"] = rounds
    return pol


def test_no_time_left_closes_on_the_standing_offer(case):
    d = _policy(case, 0).decide("sup:T1", _round2(46.0, 50.0))
    assert d.kind == "done" and "cap" in d.reason


def test_few_rounds_left_mean_a_bigger_step(case):
    threads = _round2(46.0, 50.0)
    slow = _policy(case, 3).decide("sup:T1", threads)
    free = _policy(case, None).decide("sup:T1", _round2(46.0, 50.0))
    assert slow.kind == free.kind == "send"
    assert slow.sheets[0]["unit_price"] >= free.sheets[0]["unit_price"]
    assert "before the cap" in slow.reason


def test_the_last_round_sends_one_closing_package(case):
    d = _policy(case, 1).decide("sup:T1", _round2(46.0, 50.0))
    assert d.kind == "send" and d.lines.get("closing") and len(d.sheets) == 1
    assert d.sheets[0]["unit_price"] < 50.0


def test_time_is_ignored_while_the_round_limit_binds(case):
    threads = _round2(46.0, 50.0)
    case["round_limit"] = 3
    a = _policy(case, 5).decide("sup:T1", threads)
    b = _policy(case, None).decide("sup:T1", _round2(46.0, 50.0))
    assert a.sheets == b.sheets


class SlowArena:
    """One supplier that concedes 1 % a round and replies after `latency` simulated seconds."""

    def __init__(self, case, clock, latency):
        self.case, self.clock, self.latency = case, clock, latency
        self.events, self.price, self.n = [], OPEN["unit_price"], 0
        self.replies, self.closed_at, self.result = [], None, None

    def offer(self, status="open"):
        self.n += 1
        return supplier_msg("sup:T1", self.n, {**OPEN, "unit_price": round(self.price, 2)}, status=status)

    def open_session(self, _cid, **_kw):
        self.events = [supplier_msg("sup:T1", 0, OPEN, standard=STD)]
        return {"session_id": "sim1", "case": self.case, "threads": {"sup:T1": list(self.events)}}

    def send_message(self, _sid, sup, msg, **_kw):
        action = msg.get("action")
        if action:
            self.closed_at = self.clock.t
            self.result = {"deal": action["type"] == "accept", "end_reason": action["type"]}
            return {"result": self.result}
        self.clock.advance(self.latency)
        self.price *= 0.99
        reply = self.offer()
        self.replies.append(self.clock.t)
        self.events += [{**msg, "from": "buyer"}, reply]
        return reply

    def thread(self, _sid, _sup, **_kw):
        last = self.events[-1]["term_sheets"][0]["term_sheet_id"]
        return {"messages": list(self.events), "offer_id": last, "timeouts": [], "closed": None}

    def session(self, _sid, **_kw):
        return {"status": "ended" if self.result else "open", "result": self.result}


@pytest.mark.parametrize("latency", [5, 40, 80, 110])
def test_a_slow_supplier_never_leaves_the_case_open_at_the_cap(case, latency, monkeypatch, tmp_path):
    clock = Clock()
    fake = types.SimpleNamespace(time=clock.time, monotonic=clock.monotonic, sleep=clock.sleep)
    monkeypatch.setattr(runner, "time", fake)
    monkeypatch.setattr(pacing, "time", fake)
    monkeypatch.setattr(pacing, "SHARED", pacing.Pool())
    monkeypatch.setenv("PARLEY_JOURNAL_DIR", str(tmp_path))
    cap = clock.t + 480
    arena = SlowArena(case, clock, latency)
    r = runner.CaseRunner(arena, case["case_id"], None, case=case, deadline=cap)
    r.pace.shared = None
    r.play()
    close_at = cap - runner.CLOSE_BEFORE_CAP_S
    assert arena.result and arena.result["deal"], "a valid offer stood: the case must close on it"
    assert arena.closed_at <= close_at + 1
    assert all(t <= close_at for t in arena.replies), "no reply may land after the close point"
    if latency >= 80:
        assert len(arena.replies) <= (close_at - (cap - 480)) // latency


def test_a_steady_mover_is_not_offered_more_than_its_next_step(case):
    d = _policy(case, 1).decide("sup:T1", _round2(46.0, 50.0))
    assert d.kind == "send" and d.sheets[0]["unit_price"] <= 50.0 - (52.9 - 50.0) + 0.05


def test_an_announced_step_is_waited_for_even_when_time_binds(case):
    pol = _policy(case, 1)
    pol.extra[("sup:T1", 2)] = {"step_announced": True}
    d = pol.decide("sup:T1", _round2(46.0, 50.0))
    assert "bigger step" not in d.reason


def test_a_later_landing_still_closes_before_the_cap(case, monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "LAND_S", 45.0)
    clock = Clock()
    fake = types.SimpleNamespace(time=clock.time, monotonic=clock.monotonic, sleep=clock.sleep)
    monkeypatch.setattr(runner, "time", fake)
    monkeypatch.setattr(pacing, "time", fake)
    monkeypatch.setenv("PARLEY_JOURNAL_DIR", str(tmp_path))
    cap = clock.t + 480
    arena = SlowArena(case, clock, 80)
    r = runner.CaseRunner(arena, case["case_id"], None, case=case, deadline=cap)
    r.pace.shared = None
    r.play()
    assert arena.result["deal"] and arena.closed_at <= cap - 45 + 1
    assert all(t <= cap - 45 for t in arena.replies)


def test_bigmove_hold_keeps_the_small_reply_while_rounds_remain(case, monkeypatch):
    monkeypatch.setenv("PARLEY_PACE_BIGMOVE_HOLD", "1")
    threads = _round2(46.0, 48.5)
    assert "bigger step" not in _policy(case, 3).decide("sup:T1", threads).reason
    assert "bigger step" in _policy(case, 1).decide("sup:T1", _round2(46.0, 48.5)).reason
