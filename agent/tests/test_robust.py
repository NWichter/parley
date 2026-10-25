"""Final-run failure modes: slow documents, hanging calls at the cap, 429 without a free slot, failing
closes, parked RFQ bidders, missing quote PDFs, missing data, shared SQLite across threads."""

import sqlite3
import threading
import time

import httpx
import pytest
from conftest import buyer_msg, supplier_msg

from parley import cli, runner
from parley.dossier import Dossier
from parley.kit.client import DEADLINE_STATUS, ArenaClient, ArenaError
from parley.kit.data import DataPackage
from parley.policy import CasePolicy
from parley.runner import CaseRunner, Unfinished, play_waves
from parley.valuation import Valuation

OPEN = {"unit_price": 52.9, "payment_days": 30, "contract_months": 12, "effective_date": "2026-12-01"}
STD = {"volume_per_year": 4000, "ppm_commitment": 650, "chargeback_eur_per_complaint": 0.0, "tooling_eur": 0.0}


class FakeClient:
    """The arena for one case: attachments with a delay each, messages that may hang, closes that may fail."""

    def __init__(self, case, threads, doc_delay=None, send_hang=0.0, close_errors=(), session_status="open"):
        self.case, self.threads = case, threads
        self.doc_delay = doc_delay or {}
        self.send_hang = send_hang
        self.close_errors = list(close_errors)
        self.session_status = session_status
        self.calls, self.lock_seen = [], []
        self.runner = None

    def open_session(self, case_id, **kw):
        return {"session_id": "s1", "case": self.case, "threads": {s: list(m) for s, m in self.threads.items()}}

    def attachment(self, session_id, aid, **kw):
        if self.runner is not None:
            self.lock_seen.append(self.runner.lock.locked())
        time.sleep(self.doc_delay.get(aid, 0))
        return f"text of {aid}".encode()

    def send_message(self, session_id, sid, message, **kw):
        self.calls.append(("send", sid, message, kw))
        if "action" in message:
            if self.close_errors:
                raise self.close_errors.pop(0)
            self.session_status = "ended"
            return {"result": {"deal": message["action"]["type"] == "accept", "end_reason": "accepted", "score": 0.5}}
        time.sleep(self.send_hang)
        return supplier_msg(sid, len(self.calls), {**OPEN, "unit_price": 52.0})

    def session(self, session_id, **kw):
        self.calls.append(("session", kw))
        return {"status": self.session_status, "threads": list(self.threads)}

    def thread(self, session_id, sid, **kw):
        return {"messages": list(self.threads[sid])}


def att(aid, name="quote-x.pdf"):
    return {"attachment_id": aid, "name": name}


# ---- 1. attachments ------------------------------------------------------------------------------


def test_slow_document_does_not_hold_the_round(case, monkeypatch):
    monkeypatch.setattr(runner, "DOC_WAIT_S", 0.3)
    msg = {"from": "x", "attachments": [att("att:fast", "a.txt"), att("att:slow", "b.txt")]}
    r = CaseRunner(FakeClient(case, {}, doc_delay={"att:slow": 1.5}), case["case_id"], None)
    r.session_id, r.threads = "s1", {"sup:T1": [msg]}
    t0 = time.monotonic()
    r.fetch_docs(msg, wait=runner.DOC_WAIT_S)
    assert time.monotonic() - t0 < 1.0
    assert msg["attachments"][0]["text"] == "text of att:fast"
    assert "text" not in msg["attachments"][1]
    time.sleep(1.5)
    r.fill_docs()
    assert msg["attachments"][1]["text"] == "text of att:slow"


def test_documents_download_in_parallel_and_once(case):
    client = FakeClient(case, {}, doc_delay={f"att:{i}": 0.5 for i in range(4)})
    r = CaseRunner(client, case["case_id"], None)
    r.session_id = "s1"
    msgs = [{"attachments": [att(f"att:{i}", "a.txt")]} for i in range(4)]
    t0 = time.monotonic()
    r.fetch_docs(*msgs, wait=5)
    assert time.monotonic() - t0 < 1.5
    again = [{"attachments": [att(f"att:{i}", "a.txt")]} for i in range(4)]
    t0 = time.monotonic()
    r.fetch_docs(*again, wait=5)
    assert time.monotonic() - t0 < 0.1 and all(m["attachments"][0]["text"] for m in again)


def test_close_re_read_downloads_outside_the_lock(case):
    threads = {"sup:T1": [{**supplier_msg("sup:T1", 0, OPEN, standard=STD), "attachments": [att("att:new")]}]}
    client = FakeClient(case, threads)
    r = CaseRunner(client, case["case_id"], None)
    client.runner = r
    r.session_id, r.case, r.threads = "s1", case, {s: list(m) for s, m in threads.items()}
    r.policy = CasePolicy(case, Dossier(case, None))
    r.refresh("test")
    assert client.lock_seen == [False]


def test_broken_document_gets_no_text_after_its_tries(case):
    class Broken(FakeClient):
        def attachment(self, *a, **kw):
            raise httpx.ReadTimeout("slow")

    r = CaseRunner(Broken(case, {}), case["case_id"], None)
    r.session_id = "s1"
    msg = {"attachments": [att("att:x")]}
    for _ in range(runner.DOC_TRIES + 1):
        r.fetch_docs(msg, wait=2)
    assert msg["attachments"][0]["text"] == ""


# ---- 2. the cap ----------------------------------------------------------------------------------


def test_play_closes_at_the_cap_without_waiting_for_a_hanging_call(case, monkeypatch):
    monkeypatch.setattr(runner, "CLOSE_BEFORE_CAP_S", 2)
    monkeypatch.setattr(runner, "ROUND_MARGIN_S", 0)
    monkeypatch.setattr(runner, "SEND_RESERVE_S", 0.5)
    threads = {"sup:T1": [supplier_msg("sup:T1", 0, OPEN, standard=STD)]}
    client = FakeClient(case, threads, send_hang=8)
    cap = time.time() + 4
    t0 = time.monotonic()
    result = CaseRunner(client, case["case_id"], None, case=case, deadline=lambda: cap).play()
    assert time.monotonic() - t0 < 5
    assert result.get("end_reason") == "accepted"
    sends = [c for c in client.calls if c[0] == "send" and "action" not in c[2]]
    assert sends and sends[0][3]["deadline"] <= cap and sends[0][3]["timeout"] <= runner.SEND_TIMEOUT_S


def test_client_call_never_runs_past_its_deadline():
    def handler(request):
        return httpx.Response(200, json={})

    c = ArenaClient("http://arena.test", "k", transport=httpx.MockTransport(handler), rate=1000)
    with pytest.raises(ArenaError) as e:
        c.session("s1", deadline=time.time() - 1)
    assert e.value.status == DEADLINE_STATUS


# ---- 3. 429 without a free slot, case list errors -------------------------------------------------


def test_long_retry_after_is_raised_with_its_wait():
    def handler(request):
        return httpx.Response(429, json={"detail": "too many open sessions"}, headers={"Retry-After": "30"})

    c = ArenaClient("http://arena.test", "k", transport=httpx.MockTransport(handler), rate=1000)
    with pytest.raises(ArenaError) as e:
        c.open_session("case:R1-X")
    assert e.value.status == 429 and e.value.retry_after == 30


class WaveClient:
    def __init__(self, errors=()):
        self.errors, self.t0, self.polls = list(errors), time.time(), 0

    def cases(self, set_):
        self.polls += 1
        if self.errors:
            raise self.errors.pop(0)
        open_ = time.time() - self.t0 < 1.5
        return [{"case_id": "case:R1-A", "wave": 1, "open": open_, "seconds_left": 60 if open_ else None}]


def test_a_case_without_a_slot_is_opened_again_until_it_plays(monkeypatch):
    monkeypatch.setattr(runner, "OPEN_MARGIN_S", 0)
    tries = []

    def new_runner(case, deadline):
        tries.append(time.time())
        if len(tries) < 3:
            raise ArenaError(429, "too many open sessions", retry_after=0.1)
        if len(tries) == 3:
            raise Unfinished("still open")
        return {"deal": True, "score": 0.7}

    client = WaveClient(errors=[ArenaError(429, "slow down", retry_after=1), ArenaError(503, "busy"), httpx.ConnectError("x")])
    out = play_waves(client, new_runner, waves=1, parallel=9, poll=0.05)
    assert out["case:R1-A"] == {"deal": True, "score": 0.7} and len(tries) == 4


def test_play_waves_still_raises_on_a_bad_key():
    with pytest.raises(ArenaError):
        play_waves(WaveClient(errors=[ArenaError(401, "unknown key")]), lambda c, d: {}, waves=1, poll=0.01)


def test_final_play_raises_unfinished_while_the_session_is_open(case, monkeypatch):
    monkeypatch.setattr(runner, "CLOSE_BEFORE_CAP_S", 2)
    monkeypatch.setattr(runner, "ROUND_MARGIN_S", 0)
    monkeypatch.setattr(runner, "CLOSE_MARGIN_S", 1)
    threads = {"sup:T1": [supplier_msg("sup:T1", 0, OPEN, standard=STD)]}
    client = FakeClient(case, threads, send_hang=0.2, close_errors=[ArenaError(503, "busy")] * 50)
    cap = time.time() + 3
    with pytest.raises(Unfinished):
        CaseRunner(client, case["case_id"], None, case=case, deadline=lambda: cap).play()


# ---- 4. the close --------------------------------------------------------------------------------


def test_close_retries_with_backoff_through_errors_and_a_failing_re_read(case, monkeypatch):
    threads = {"sup:T1": [supplier_msg("sup:T1", 0, OPEN, standard=STD)]}
    errors = [ArenaError(503, "busy"), httpx.ReadTimeout("slow"), ArenaError(429, "slow down", retry_after=0.2)]
    client = FakeClient(case, threads, close_errors=errors)
    sessions = iter([RuntimeError("down")])

    def session(sid, **kw):
        try:
            raise next(sessions)
        except StopIteration:
            return {"status": "open", "threads": ["sup:T1"]}

    client.session = session
    r = CaseRunner(client, case["case_id"], None, case=case)
    r.open()
    r.policy = CasePolicy(case, Dossier(case, None))
    r.policy.replied_at["sup:T1"] = time.time()
    t0 = time.monotonic()
    r.finish(r.policy.close(r.threads, "test"))
    closes = [c for c in client.calls if c[0] == "send" and "action" in c[2]]
    assert r.ended and r.result["end_reason"] == "accepted" and len(closes) == 4
    assert time.monotonic() - t0 >= 1.0


def test_close_gives_up_before_the_cap(case, monkeypatch):
    monkeypatch.setattr(runner, "CLOSE_MARGIN_S", 1)
    threads = {"sup:T1": [supplier_msg("sup:T1", 0, OPEN, standard=STD)]}
    client = FakeClient(case, threads, close_errors=[ArenaError(503, "busy")] * 50)
    cap = time.time() + 4
    r = CaseRunner(client, case["case_id"], None, case=case, deadline=lambda: cap)
    r.open()
    r.policy = CasePolicy(case, Dossier(case, None))
    r.policy.replied_at["sup:T1"] = time.time()
    r.finish(r.policy.close(r.threads, "test"))
    assert time.time() < cap and not r.ended


# ---- 6. no quote PDF -----------------------------------------------------------------------------


def test_without_standard_terms_an_open_must_have_counts_as_broken(case):
    case["buyer"]["must_haves"] = [{"field": "ppm_commitment", "op": "<=", "value": 700}]
    val = Valuation(case)
    assert val.keeps_must_haves(OPEN, {k: v for k, v in STD.items()})
    assert not val.keeps_must_haves(OPEN, {})
    case["buyer"]["must_haves"] = [{"field": "payment_days", "op": ">=", "value": 30}]
    assert Valuation(case).keeps_must_haves(OPEN, {})


def test_unreadable_quote_never_leads_to_an_accept_on_guessed_standards(case):
    case["buyer"]["must_haves"] = [{"field": "ppm_commitment", "op": "<=", "value": 700}]
    msg = supplier_msg("sup:T1", 0, OPEN)
    msg["attachments"] = [{"attachment_id": "att:q", "name": "quote-offer-T1-0.pdf", "text": ""}]
    pol = CasePolicy(case, Dossier(case, None))
    assert pol.close({"sup:T1": [msg]}, "test").kind == "walk"
    r = CaseRunner(None, case["case_id"], None, case=case)
    assert r.safe_close({"sup:T1": [msg]}, "test").kind == "walk"
    lower = {**OPEN, "unit_price": 50.0}
    good = [supplier_msg("sup:T1", 0, OPEN, standard=STD), buyer_msg([lower]), supplier_msg("sup:T1", 1, lower, standard=STD)]
    assert CasePolicy(case, Dossier(case, None)).close({"sup:T1": good}, "test").kind == "accept"
    bad = [msg, buyer_msg([lower]), {**supplier_msg("sup:T1", 1, lower), "attachments": []}]
    assert CasePolicy(case, Dossier(case, None)).close({"sup:T1": bad}, "test").kind == "walk"


# ---- 7. missing data -----------------------------------------------------------------------------


def test_missing_data_package_exits_with_the_no_restart_code():
    with pytest.raises(SystemExit) as e:
        cli.check_data(None, None)
    assert e.value.code == cli.EXIT_NO_DATA


# ---- 8. one SQLite connection, many threads -------------------------------------------------------


def make_package(tmp_path):
    db = sqlite3.connect(tmp_path / "data.sqlite")
    db.execute("CREATE TABLE documents (id TEXT, supplier_id TEXT, date TEXT, kind TEXT, subject TEXT, file TEXT)")
    db.execute("CREATE TABLE news (id TEXT, supplier_id TEXT, date TEXT, topic TEXT, headline TEXT, body TEXT)")
    (tmp_path / "docs").mkdir()
    for i in range(300):
        db.execute("INSERT INTO documents VALUES (?, ?, ?, 'note', 's', ?)", (f"doc:{i}", f"sup:{i % 7}", f"2026-0{1 + i % 9}-01", f"docs/{i}.txt"))
        (tmp_path / "docs" / f"{i}.txt").write_text(f"note {i}", encoding="utf-8")
        db.execute("INSERT INTO news VALUES (?, ?, '2026-05-01', 'other', 'h', 'b')", (f"news:{i}", f"sup:{i % 7}"))
    db.commit()
    db.close()
    (tmp_path / "facts.json").write_text("{}", encoding="utf-8")
    return DataPackage(tmp_path)


def test_package_survives_many_threads(tmp_path):
    pkg = make_package(tmp_path)
    errors = []

    def hammer(k):
        try:
            for i in range(60):
                rows = pkg.query("SELECT id, file FROM documents WHERE supplier_id = ?", (f"sup:{(k + i) % 7}",), limit=500)
                assert all(r["id"].startswith("doc:") and r["file"] for r in rows)
                assert pkg.document(rows[0]["id"]).startswith("note ")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=hammer, args=(k,)) for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors


def test_profile_survives_a_failing_document(tmp_path, case):
    pkg = make_package(tmp_path)

    def broken(ref):
        raise TypeError("unsupported operand type(s) for /: 'WindowsPath' and 'NoneType'")

    pkg.document = broken
    case["suppliers"] = ["sup:1"]
    d = Dossier(case, pkg)
    assert d.profiles["sup:1"].supplier_id == "sup:1"
    with pytest.raises(FileNotFoundError):
        DataPackage(tmp_path).document(None)


def test_profile_survives_a_failing_query(case):
    class Pkg:
        db, facts, release = object(), {}, None

        def query(self, *a, **k):
            raise RuntimeError("boom")

        def lookup(self, ref):
            return None

    d = Dossier({**case, "suppliers": ["sup:T1"]}, Pkg())
    assert "sup:T1" in d.profiles


def test_parked_keep_alive_is_sent_once(rfq_case):
    std = {"volume_per_year": 6000, "incoterm": "EXW", "ppm_commitment": 1100}
    t = {"unit_price": 16.0, "payment_days": 30, "lead_time_days": 35, "incoterm": "EXW"}
    threads = {
        "sup:A": [supplier_msg("sup:A", 0, t, standard=std), buyer_msg([t]), supplier_msg("sup:A", 1, {**t, "unit_price": 13.0})],
        "sup:B": [supplier_msg("sup:B", 0, {**t, "unit_price": 18.0}, standard=std), buyer_msg([t]), supplier_msg("sup:B", 1, {**t, "unit_price": 17.5})],
    }
    pol = CasePolicy(rfq_case, Dossier(rfq_case, None))
    assert pol.decide("sup:B", threads).lines.get("park")
    assert pol.decide("sup:B", threads).kind == "done"


# ---- rehearsal findings: 429 bursts on a send, a close still in flight ------------------------------


def test_short_429_burst_is_waited_out_until_the_deadline():
    calls = []

    def handler(request):
        calls.append(time.time())
        if len(calls) <= 6:
            return httpx.Response(429, json={"detail": "more than 10 calls per second"}, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"ok": True})

    c = ArenaClient("http://arena.test", "k", transport=httpx.MockTransport(handler), rate=1000)
    assert c.send_message("s1", "sup:A", {"body": "x"}, deadline=time.time() + 10) == {"ok": True}
    calls.clear()
    with pytest.raises(ArenaError):
        c.send_message("s1", "sup:A", {"body": "x"})


def test_a_429_burst_on_a_send_does_not_lose_the_thread(case):
    threads = {"sup:T1": [supplier_msg("sup:T1", 0, OPEN, standard=STD)]}

    class Bursty(FakeClient):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.burst = 2

        def send_message(self, session_id, sid, message, **kw):
            if "action" not in message and self.burst:
                self.burst -= 1
                raise ArenaError(429, "more than 10 calls per second", retry_after=0.1)
            return super().send_message(session_id, sid, message, **kw)

    client = Bursty(case, threads)
    r = CaseRunner(client, case["case_id"], None, case=case)
    r.open()
    r.policy = CasePolicy(case, Dossier(case, None))
    r.policy.replied_at["sup:T1"] = time.time()
    t = threading.Thread(target=r.worker, args=("sup:T1",))
    t.start()
    deadline = time.time() + 20
    while time.time() < deadline and len(r.threads["sup:T1"]) < 3 and t.is_alive():
        time.sleep(0.05)
    r.closing.set()
    t.join(timeout=10)
    assert client.burst == 0 and len(r.threads["sup:T1"]) >= 3 and "sup:T1" not in r.done


def test_play_waits_for_a_close_still_in_flight(case, monkeypatch):
    threads = {"sup:T1": [supplier_msg("sup:T1", 0, OPEN, standard=STD)]}

    class SlowClose(FakeClient):
        def send_message(self, session_id, sid, message, **kw):
            if "action" in message:
                time.sleep(1.0)
            return super().send_message(session_id, sid, message, **kw)

    client = SlowClose(case, threads)
    cap = time.time() + 300
    r = CaseRunner(client, case["case_id"], None, case=case, deadline=lambda: cap)
    r.open()
    r.policy = CasePolicy(case, Dossier(case, None))
    d = r.policy.close(r.threads, "test")
    t = threading.Thread(target=r.finish, args=(d,))
    t.start()
    time.sleep(0.2)
    monkeypatch.setattr(r, "_play", lambda: None)
    result = r.play()
    t.join()
    assert r.ended and result.get("end_reason") == "accepted"


def test_a_send_waits_for_a_slow_supplier_reply():
    assert runner.SEND_TIMEOUT_S >= 180
