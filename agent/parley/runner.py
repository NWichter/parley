"""Play cases against the arena. One worker per supplier thread, so no thread waits on another
supplier's reply; a coordinator closes the case. Every decision goes to a JSONL journal with its
reason."""

import functools
import json
import logging
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait as wait_futures
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

from . import analyst, rules, writer
from .dossier import ROOT, Dossier, _profile_fields_file, apply_persona
from .kit.client import DEADLINE_STATUS, ArenaError
from .kit.cost import total_cost
from .kit.docs import read_document, standard_terms
from .llm import Tagged
from .pacing import Pace
from .policy import ENDED, CasePolicy, Decision, flag, supplier_messages
from .prepare import data_version, release_suppliers
from .valuation import must_haves_safe

log = logging.getLogger("parley")

CLOSE_BEFORE_CAP_S, ROUND_MARGIN_S = 90, 15
LAND_S = float(os.environ.get("PARLEY_PACE_LAND_S") or CLOSE_BEFORE_CAP_S)
DECISION_BUDGET_S = 40
FRESH_OFFER_S = 30
# Attachments are read inside our 60 s: in parallel, each download bounded, cached; what is not there
# in time is filled in later and the round goes on without it.
DOC_TIMEOUT_S, DOC_WAIT_S, DOC_WORKERS, DOC_TRIES = 12, 10, 4, 2
# One supplier call may not hold a worker for long, and none runs into the close.
# Supplier replies take up to ~100 s (p90 61-74 s live): wait for the reply, bounded by the cap,
# instead of timing out and re-sending a move the arena is still answering.
SEND_TIMEOUT_S, SEND_RESERVE_S = 180, 30
# The close: retries with backoff until shortly before the cap (without a cap, a few attempts).
CLOSE_MARGIN_S, CLOSE_TIMEOUT_S, CLOSE_TRIES_NO_CAP = 3, 30, 6
# A case without a session scores 0: retry opening (and resume after an error) until the cap.
OPEN_MARGIN_S, OPEN_BACKOFF_MAX_S = 5, 15
RUNNING = re.compile(r"one session per case in the final: (\S+)")
JOURNAL_DIR = ROOT / "runs" / "journal"
ARENA_HOST = "arena.tacto.ai"


def journal_dir():
    return Path(os.environ.get("PARLEY_JOURNAL_DIR") or JOURNAL_DIR)


def journal_name(session_id):
    """Sessions of a local simulator restart at sim1: prefix them with its host and port."""
    host = urlparse(os.environ.get("ARENA_URL", "")).netloc
    if host and host != ARENA_HOST:
        return f"{re.sub(r'[^A-Za-z0-9.-]', '-', host)}_{session_id}.jsonl"
    return f"{session_id}.jsonl"


class Unfinished(Exception):
    """The case's session is still open after play(): the caller resumes it."""


class Journal:
    def __init__(self, case_id, path=None):
        self.case_id = case_id
        self.lock = threading.Lock()
        self.path = path
        self.buffer = []

    def bind(self, session_id):
        folder = journal_dir()
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / journal_name(session_id)
        for row in self.buffer:
            self._write(row)
        self.buffer = []

    def log(self, event, **data):
        row = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), "case": self.case_id, "event": event, **data}
        with self.lock:
            if self.path:
                self._write(row)
            else:
                self.buffer.append(row)

    def _write(self, row):
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


class CaseRunner:
    def __init__(self, client, case_id, package, llm=None, case=None, deadline=None, personas=None, analyst_llm=True,
                 mode="full"):
        self.client, self.case_id, self.package = client, case_id, package
        self.llm = Tagged(llm, case_id) if llm else None
        self.case, self.deadline = case, deadline
        self.personas = personas or {}
        self.analyst_llm = analyst_llm
        self.mode = mode if llm else "nollm"
        self.session_id = None
        self.lock = threading.Lock()
        self.closing = threading.Event()
        self.close_started, self.close_done = False, threading.Event()
        self.result = None
        self.ended = False
        self.threads = {}
        self.done = set()
        self.held = False
        self.slowest = 0.0
        self.pace = Pace()
        self.inflight = set()
        self.journal = Journal(case_id)
        self.pool = None
        self.docs, self.doc_jobs, self.doc_tries = {}, {}, {}
        self.doc_lock = threading.Lock()
        self.doc_pool = None

    # ---- arena -------------------------------------------------------------------------------

    def open(self):
        try:
            r = self.client.open_session(self.case_id)
        except ArenaError as e:
            if e.status != 409:
                raise
            m = RUNNING.search(str(e.detail))
            sid = m.group(1) if m else self.find_running()
            if not sid:
                raise
            return self.resume(sid)
        self.session_id = r["session_id"]
        self.case = r["case"]
        self.threads = {s: list(msgs) for s, msgs in r["threads"].items()}
        self.journal.bind(self.session_id)
        if self.llm:
            self.llm.tag = f"{self.case_id}#{self.session_id}"
        self.journal.log("open", session=self.session_id)
        self.journal.log("case", case=self.case)
        return True

    def find_running(self):
        """After a 409 whose wording we do not recognise: our open session of this case."""
        for set_ in ("final", "practice", "sandbox"):
            try:
                rows = self.client.sessions(set_)
            except ArenaError:
                continue
            for row in rows or []:
                if row.get("case_id") == self.case_id and row.get("status") == "open":
                    return row["session_id"]
        return None

    def resume(self, session_id):

        self.session_id = session_id
        self.journal.bind(session_id)
        if self.llm:
            self.llm.tag = f"{self.case_id}#{session_id}"
        state = self.client.session(session_id)
        self.journal.log("resume", session=session_id, status=state.get("status"))
        if state.get("status") == "ended":
            self.result = state.get("result") or {"deal": False, "end_reason": state.get("end_reason")}
            self.ended = True
            return False
        self.case = self.case or state.get("case")
        raw = {s: self.client.thread(session_id, s) for s in state["threads"]}
        self.threads = {s: self.thread_events(t, doc_wait=0) for s, t in raw.items()}
        self.fetch_docs(*[m for ev in self.threads.values() for m in ev])
        return True

    def thread_events(self, t, doc_wait=DOC_WAIT_S):
        """A thread as the arena has it now. After a timeout the arena repeats our last offer and the
        supplier's answer only appears under `timeouts`, with a new standing offer ID: add it as the
        latest supplier message, so we never accept a stale ID."""
        events = list(t.get("messages") or [])
        self.fetch_docs(*events, wait=doc_wait)
        last = (t.get("timeouts") or [None])[-1]
        current = t.get("offer_id")
        known = {ts.get("term_sheet_id") for m in events for ts in m.get("term_sheets") or []}
        if last and current and current not in known:
            events.append({
                "from": "arena-timeout",
                "status": "walked_away" if t.get("closed") else (last.get("status") or t.get("status")),
                "body": last.get("text") or "",
                "term_sheets": [] if t.get("closed") else [{"term_sheet_id": current, "terms": last.get("terms") or {}}],
                "missing_fields": last.get("missing_fields") or [],
                "claim_results": [],
                "attachments": [],
            })
        return events

    def fetch_docs(self, *msgs, wait=DOC_WAIT_S):
        """Put each attachment's text into it. Downloads run in parallel, each at most DOC_TIMEOUT_S,
        and are cached; after `wait` s we go on with what we have: a document still on its way stays
        without text and `fill_docs` adds it later. A document that cannot be read gets no text."""
        pending = []
        for msg in msgs:
            for att in msg.get("attachments") or []:
                job = self._doc_job(att)
                if job is not None:
                    pending.append((att, job))
        if pending and wait:
            wait_futures([j for _, j in pending], timeout=wait)
        for att, job in pending:
            if job.done():
                self._settle(att, job)
        return msgs[0] if len(msgs) == 1 else None

    def fill_docs(self):
        """Non-blocking: attachments whose download has finished since get their text."""
        with self.lock:
            msgs = [m for ev in self.threads.values() for m in ev]
        self.fetch_docs(*msgs, wait=0)

    def _doc_job(self, att):
        aid = att.get("attachment_id")
        if not aid or "text" in att:
            return None
        with self.doc_lock:
            if aid in self.docs:
                att["text"] = self.docs[aid]
                return None
            job = self.doc_jobs.get(aid)
            if job is None:
                if self.doc_tries.get(aid, 0) >= DOC_TRIES:
                    att["text"] = ""
                    return None
                self.doc_tries[aid] = self.doc_tries.get(aid, 0) + 1
                if self.doc_pool is None:
                    self.doc_pool = ThreadPoolExecutor(max_workers=DOC_WORKERS, thread_name_prefix="docs")
                job = self.doc_jobs[aid] = self.doc_pool.submit(self._download, aid, att.get("name") or "")
        return job

    def _download(self, aid, name):
        data = self.client.attachment(self.session_id, aid, timeout=DOC_TIMEOUT_S, deadline=time.time() + DOC_TIMEOUT_S)
        return read_document(name, data)

    def _settle(self, att, job):
        aid = att["attachment_id"]
        try:
            text = job.result()
        except Exception as e:  # noqa: BLE001 - a broken document must not stop the case
            log.warning("attachment %s: %r", aid, e)
            with self.doc_lock:
                if self.doc_jobs.get(aid) is job:
                    del self.doc_jobs[aid]
                failed = self.doc_tries.get(aid, 0) >= DOC_TRIES
            if failed:
                att["text"] = ""
            return
        with self.doc_lock:
            self.docs[aid] = text
        att["text"] = text

    def cap(self):
        return self.deadline() if callable(self.deadline) else self.deadline

    def call_deadline(self, reserve=SEND_RESERVE_S):
        cap = self.cap()
        return cap - reserve if cap else None

    def close_deadline(self):
        cap = self.cap()
        return cap - CLOSE_MARGIN_S if cap else None

    def close_at(self):
        cap = self.cap()
        return cap - CLOSE_BEFORE_CAP_S if cap else None

    def land_at(self):
        """The latest a reply may land: the close point, or later with PARLEY_PACE_LAND_S."""
        cap = self.cap()
        return cap - min(LAND_S, CLOSE_BEFORE_CAP_S) if cap else None

    def near_cap(self):
        at = self.close_at()
        if not at or time.time() <= at:
            return False
        return not self.inflight or time.time() > self.land_at()

    def time_rounds(self, sid):
        return self.pace.rounds(sid, self.land_at())

    # ---- the case ----------------------------------------------------------------------------

    def play(self):
        try:
            self._play()
            if self.close_started and not self.close_done.is_set():
                dl = self.close_deadline()
                self.close_done.wait(timeout=max(1.0, dl - time.time()) if dl else 120)
        finally:
            self.closing.set()
            for pool in (self.pool, self.doc_pool):
                if pool:
                    pool.shutdown(wait=False, cancel_futures=True)
        if not self.ended and self.session_id:
            try:
                state = self.client.session(self.session_id, timeout=15)
            except Exception as e:  # noqa: BLE001
                state = {}
                self.journal.log("close_error", where="final state", error=repr(e))
            if state.get("status") == "ended":
                self.ended = True
                self.result = state.get("result") or self.result or {"deal": False, "end_reason": state.get("end_reason")}
            elif self.deadline is not None:
                raise Unfinished(f"{self.case_id}: session {self.session_id} is still open")
        return self.result or {"deal": False, "end_reason": None}

    def build_dossier(self):
        for attempt in (1, 2):
            try:
                return Dossier(self.case, self.package)
            except Exception as e:
                self.journal.log("dossier_error", attempt=attempt, error=f"{type(e).__name__}: {e}")
                time.sleep(0.5)
        return Dossier(self.case, None)

    def _play(self):
        if not self.open():
            return
        self.fetch_docs(*[m for msgs in self.threads.values() for m in msgs])
        rules.bind(rules.derive(self.case, _profile_fields_file()))
        if rules.current():
            self.journal.log("rules", on=rules.current())
        self.dossier = self.build_dossier()
        for sid, persona in self.personas.items():
            if sid in self.dossier.profiles and persona:
                apply_persona(self.dossier.profiles[sid], persona)
        self.policy = CasePolicy(self.case, self.dossier)
        now = time.time()
        for sid in self.threads:
            self.policy.replied_at[sid] = now
        self.journal.log("dossier", **self.dossier.summary())
        live = [
            s
            for s, ev in self.threads.items()
            if supplier_messages(ev) and supplier_messages(ev)[-1].get("status") not in ENDED
        ]
        if not live:
            self.finish(self.safe_close(self.threads, "no supplier quoted"))
            return
        # No `with` block: at the cap we close and return; a worker still in a call does not hold the case.
        self.pool = ThreadPoolExecutor(
            max_workers=len(live), thread_name_prefix="case", initializer=rules.bind, initargs=(rules.current(),)
        )
        first = self.first_moves(live)
        futures = [self.pool.submit(self.worker, sid, False, first.get(sid)) for sid in live]
        while True:
            self.wait_workers(futures)
            if self.result is not None:
                return
            with self.lock:
                snapshot = {s: list(ev) for s, ev in self.threads.items()}
            reason = "time is nearly up" if self.near_cap() else "every thread has finished"
            d = self.safe_close(snapshot, reason, hold_ok=not self.closing.is_set())
            if d.kind != "hold":
                self.finish(d)
                return
            self.journal.log("hold", reason=d.reason)
            with self.lock:
                self.done -= set(d.metrics["live"])
            futures = [self.pool.submit(self.worker, sid, True) for sid in d.metrics["live"]]

    def wait_workers(self, futures):
        while not self.closing.is_set():
            if all(f.done() for f in futures):
                return
            if self.near_cap():
                self.journal.log("cap", seconds_left=round(self.cap() - time.time(), 1))
                return
            try:
                with self.lock:
                    final = self.policy.rfq and self.policy.final_is_best(self.threads)
            except Exception as e:  # noqa: BLE001 - the coordinator must reach the close
                log.exception("%s: final_is_best failed", self.case_id)
                self.journal.log("error", where="final_is_best", error=repr(e))
                final = False
            if final:
                self.journal.log("final_is_best", note="the best offer is a final one: close before it expires")
                return
            time.sleep(0.5)

    def decide(self, sid, snapshot, anchor=False):
        """PARLEY_SAFE_CLOSE: a failing decision is tried once more; in round 0 the anchor sheet
        goes out instead of losing the thread."""
        if not flag("PARLEY_SAFE_CLOSE"):
            return self.policy.decide(sid, snapshot)
        for _ in range(0 if anchor else 2):
            try:
                return self.policy.decide(sid, snapshot)
            except Exception as e:  # noqa: BLE001
                log.warning("%s %s: decide failed (%r)", self.case_id, sid, e)
                self.journal.log("error", supplier=sid, where="decide", error=repr(e))
        if any(m.get("from") == "buyer" for m in snapshot[sid]):
            raise RuntimeError("decide failed twice after round 0")
        return self.policy.anchor_decision(sid, snapshot)

    def first_moves(self, live):
        """RFQ: the first move of every bidder is decided in supplier order before any is sent, so which
        fact goes to which bidder does not depend on thread timing (as in the offline replay)."""
        if not (self.policy.rfq and flag("PARLEY_RFQ_ORDERED")):
            return {}
        with self.lock:
            snapshot = {s: list(ev) for s, ev in self.threads.items()}
        out = {}
        for sid in sorted(live):
            try:
                self.policy.time_rounds[sid] = self.time_rounds(sid)
                out[sid] = (self.decide(sid, snapshot), snapshot)
            except Exception as e:  # noqa: BLE001 - the worker decides again
                self.journal.log("error", supplier=sid, where="first move", error=repr(e))
        return out

    def worker(self, sid, anchor=False, first=None):
        try:
            while not self.closing.is_set():
                t0 = time.monotonic()
                if first:
                    d, snapshot = first
                    first = None
                else:
                    self.fill_docs()
                    with self.lock:
                        snapshot = {s: list(ev) for s, ev in self.threads.items()}
                    self.read_with_model(sid, snapshot)
                    self.policy.time_rounds[sid] = self.time_rounds(sid)
                    d = self.decide(sid, snapshot, anchor)
                anchor = False
                if d.kind in ("done",):
                    self.journal.log("thread_done", supplier=sid, reason=d.reason, metrics=d.metrics)
                    if not self.policy.rfq:
                        close = self.safe_close(snapshot, d.reason, hold_ok=True)
                        if close.kind == "hold":
                            self.journal.log("hold", supplier=sid, reason=close.reason)
                            anchor = True
                            continue
                    with self.lock:
                        self.done.add(sid)
                    if not self.policy.rfq:
                        self.finish(close)
                    elif flag("PARLEY_RFQ_CLOSE_NOW") and self.policy.take_now(sid, snapshot, d):
                        self.journal.log("rfq_take", supplier=sid, note="take condition met: close the case now")
                        self.finish(self.safe_close(snapshot, d.reason))
                    return
                if d.kind in ("accept", "walk"):
                    self.finish(d)
                    return
                if self.closing.is_set():
                    return
                if self.policy.rfq and self.policy.final_is_best(snapshot):
                    self.journal.log("hold_send", supplier=sid, note="a final offer is the best: no new message")
                    return
                msg = self.write(sid, d, snapshot)
                if self.time_rounds(sid) == 0:
                    e, p = self.pace.estimate(sid)
                    self.journal.log("thread_done", supplier=sid, reason="no time left for a reply before the close",
                                     metrics={"rtt_s": round(e, 1), "p90_s": round(p, 1)})
                    with self.lock:
                        self.done.add(sid)
                    if not self.policy.rfq:
                        self.finish(self.safe_close(snapshot, "no time left for another round"))
                    return
                took = time.monotonic() - t0
                e, p = self.pace.estimate(sid)
                d.metrics["pace"] = {"rounds": self.policy.time_rounds.get(sid), "rtt_s": round(e, 1), "p90_s": round(p, 1)}
                self.journal.log(
                    "send",
                    supplier=sid,
                    reason=d.reason,
                    metrics=d.metrics,
                    claims=[c.sentence for c in d.claims],
                    message=msg,
                    decide_s=round(took, 2),
                )
                if self.closing.is_set():
                    return
                self.inflight.add(sid)
                try:
                    reply = self.client.send_message(self.session_id, sid, msg, timeout=SEND_TIMEOUT_S,
                                                     deadline=self.call_deadline())
                except Exception as e:  # noqa: BLE001
                    self.inflight.discard(sid)
                    if getattr(e, "status", None) == 409:
                        if self.after_409(sid, e):
                            return
                        continue
                    if not self.recover_send(sid, e):
                        raise
                    continue
                self.inflight.discard(sid)
                if "result" in reply:
                    self.result = reply["result"]
                    self.ended = True
                    self.closing.set()
                    return
                self.fetch_docs(reply)
                with self.lock:
                    self.threads[sid] += [{**msg, "from": "buyer"}, reply]
                    self.policy.replied_at[sid] = time.time()
                    self.slowest = max(self.slowest, time.monotonic() - t0)
                    self.pace.add(sid, time.monotonic() - t0)
                self.journal.log(
                    "reply",
                    supplier=sid,
                    status=reply.get("status"),
                    terms=(reply.get("term_sheets") or [{}])[0].get("terms"),
                    claim_results=reply.get("claim_results"),
                    reading=analyst.read_reply(reply).to_dict(),
                )
        except Exception as e:  # noqa: BLE001 - one broken thread must not stop the case
            log.exception("%s %s failed", self.case_id, sid)
            self.journal.log("error", supplier=sid, error=repr(e))
            self.inflight.discard(sid)
            with self.lock:
                self.done.add(sid)

    def after_409(self, sid, e):
        """Our message crossed another move: re-read. True when the thread or the case is over."""
        self.journal.log("409", supplier=sid, detail=str(e.detail))
        if self.closing.is_set():
            return True
        state = self.client.session(self.session_id, deadline=self.call_deadline())
        if state.get("status") == "ended":
            self.result = state.get("result") or {"deal": False, "end_reason": state.get("end_reason")}
            self.ended = True
            self.closing.set()
            return True
        t = self.client.thread(self.session_id, sid, deadline=self.call_deadline())
        events = self.thread_events(t)
        with self.lock:
            self.threads[sid] = events
        if t.get("closed"):
            with self.lock:
                self.done.add(sid)
            return True
        return False

    def recover_send(self, sid, e):
        """A send that failed on the way (rate limit, server error, timeout, network): the thread is
        not lost. Wait a little, re-read it (the arena may have answered) and decide again. False for
        any other error, and once the case is closing or past the send deadline."""
        status = getattr(e, "status", None)
        transient = isinstance(e, httpx.TransportError) or status in (DEADLINE_STATUS, 429) or (status or 0) >= 500
        dl = self.call_deadline()
        if not transient or self.closing.is_set() or (dl and time.time() >= dl):
            return False
        self.journal.log("send_error", supplier=sid, status=status, error=repr(e))
        self.closing.wait(min(getattr(e, "retry_after", None) or 2.0, 5.0))
        if self.closing.is_set():
            return False
        try:
            t = self.client.thread(self.session_id, sid, timeout=CLOSE_TIMEOUT_S, deadline=dl)
        except Exception as e2:  # noqa: BLE001 - decide again on what we have
            self.journal.log("send_error", supplier=sid, where="re-read", error=repr(e2))
            return True
        events = self.thread_events(t, doc_wait=3)
        with self.lock:
            if len(supplier_messages(events)) > len(supplier_messages(self.threads.get(sid) or [])):
                self.policy.replied_at[sid] = time.time()
            self.threads[sid] = events
        return not t.get("closed") or self._mark_done(sid)

    def _mark_done(self, sid):
        with self.lock:
            self.done.add(sid)
        return False

    def read_with_model(self, sid, snapshot):
        """Hybrid and full mode: the model reads the supplier's latest e-mail once; its signals join the
        rule flags the policy decides on (never a number)."""
        if self.mode == "nollm" or not self.llm or not self.analyst_llm:
            return
        sup = supplier_messages(snapshot[sid])
        if len(sup) < 2 or (sid, len(sup)) in self.policy.extra:
            return
        extra = analyst.classify(self.llm, sup[-1], timeout=6.0)
        self.policy.extra[(sid, len(sup))] = extra or {}
        rules = analyst.read_reply(sup[-1]).flags
        if extra:
            added = sorted(f for k, f in analyst.SIGNALS_MAP.items() if extra.get(k) is True and not rules.get(f))
            self.journal.log("read", supplier=sid, signals=extra, added_to_rules=added)

    def write(self, sid, d: Decision, snapshot):
        sup = supplier_messages(snapshot[sid])
        last = sup[-1] if sup else None
        reading = analyst.read_reply(last) if last else None
        first = not any(m.get("from") == "buyer" for m in snapshot[sid])
        extra = self.policy.extra.get((sid, len(sup)))
        if extra:
            reading = analyst.merge(reading, extra)
            d.metrics["analyst"] = extra
        offers = d.metrics.get("asks")
        d.metrics["moved"] = (
            len(sup) >= 2
            and bool(sup[-1].get("term_sheets"))
            and bool(sup[-2].get("term_sheets"))
            and sup[-1]["term_sheets"][0]["terms"] != sup[-2]["term_sheets"][0]["terms"]
        )
        persona = (self.personas.get(sid) or {}).get("summary")
        ours = [m for m in snapshot[sid] if m.get("from") == "buyer" and not m.get("action")]
        ctx = {
            "n": len(ours),
            "prev_openings": [writer.opening_of(m.get("body")) for m in ours],
            "history": sup,
            "key": f"{self.case_id}|{sid}",
        }
        msg, used = writer.compose(self.case, d, last, reading, first, self.llm if self.mode == "full" else None,
                                   persona, ctx)  # fmt: skip
        d.metrics["writer"] = "llm" if used else "template"
        del offers
        return msg

    def finish(self, d: Decision):
        with self.lock:
            if self.closing.is_set():
                return
            self.closing.set()
            self.close_started = True
        try:
            self._close(d)
        except Exception as e:  # noqa: BLE001 - whatever fails, the result line below is written
            log.exception("%s: close failed", self.case_id)
            self.journal.log("close_error", where="close", error=repr(e))
        finally:
            self.close_done.set()
        if self.result is None:
            try:
                self.result = self.session_result()
            except Exception as e:  # noqa: BLE001
                self.journal.log("close_error", where="session", error=repr(e))
                self.result = {"deal": False, "end_reason": None}
        self.journal.log(
            "result",
            **{
                k: self.result.get(k)
                for k in ("deal", "score", "c_open", "c_deal", "c_best", "c_alt", "supplier_id", "terms", "end_reason")
                if k in self.result
            },
            llm=self.llm.summary() if self.llm else None,
            llm_run_total=self.llm.usage.summary() if self.llm else None,
        )

    def refresh(self, reason):
        """Re-read every thread (outside the lock) and decide the close again on what stands now."""
        dl = self.close_deadline()
        raw = {s: self.client.thread(self.session_id, s, timeout=CLOSE_TIMEOUT_S, deadline=dl) for s in list(self.threads)}
        events = {s: self.thread_events(t, doc_wait=0) for s, t in raw.items()}
        self.fetch_docs(*[m for ev in events.values() for m in ev], wait=3)
        with self.lock:
            self.threads.update(events)
            snapshot = {s: list(ev) for s, ev in self.threads.items()}
        return self.safe_close(snapshot, reason)

    def _close(self, d: Decision):
        """Send the close until it lands, with backoff, until shortly before the cap. After a refusal or
        an error the session's state decides: ended, or re-read the threads and close on what stands."""
        fresh = time.time() - (self.policy.replied_at.get(d.supplier_id) or 0) < FRESH_OFFER_S if hasattr(self, "policy") else True
        if d.kind == "accept" and not fresh:
            try:
                d = self.refresh(d.reason + "; re-read before accepting")
            except Exception as e:  # noqa: BLE001
                self.journal.log("close_error", where="re-read", error=repr(e))
        attempt = 0
        while True:
            dl = self.close_deadline()
            action = {"type": "accept", "term_sheet_id": d.term_sheet_id} if d.kind == "accept" else {"type": "walk_away"}
            self.journal.log("close", supplier=d.supplier_id, action=action, reason=d.reason, metrics=d.metrics)
            try:
                r = self.client.send_message(self.session_id, d.supplier_id, {"action": action},
                                             timeout=CLOSE_TIMEOUT_S, deadline=dl)
                self.result = r.get("result", r)
                self.ended = True
                return
            except Exception as e:  # noqa: BLE001
                status, retry_after = getattr(e, "status", None), getattr(e, "retry_after", None)
                self.journal.log("close_error", status=status, detail=str(getattr(e, "detail", e)), attempt=attempt)
            attempt += 1
            pause = min(retry_after or 0.5 * 2**attempt, 8)
            if (dl and time.time() + pause > dl - 1) or (not dl and attempt >= CLOSE_TRIES_NO_CAP):
                self.journal.log("close_error", where="close", detail="no time left for another close attempt")
                return
            time.sleep(pause)
            try:
                state = self.client.session(self.session_id, timeout=CLOSE_TIMEOUT_S, deadline=dl)
                if state.get("status") == "ended":
                    self.result = state.get("result") or {"deal": False, "end_reason": state.get("end_reason")}
                    self.ended = True
                    return
                if status in (409, 422):
                    d = self.refresh("retry after a refused close")
            except Exception as e:  # noqa: BLE001 - keep the last decision and send it again
                self.journal.log("close_error", where="re-read", error=repr(e))

    def safe_close(self, threads, reason, hold_ok=False):
        """The policy's close; if that fails, the plain published cost decides (never no move).
        hold_ok: a round-0 "hold" is allowed once per case while time is left."""
        try:
            with self.lock:
                time_left = hold_ok and not self.held and not self.near_cap()
                self.held = self.held or time_left
            d = self.policy.close(threads, reason, time_left=time_left)
            if d.kind == "hold" and not time_left:
                raise RuntimeError("hold without time left")
            return d
        except Exception as e:  # noqa: BLE001
            log.exception("%s: close failed, plain fallback", self.case_id)
            self.journal.log("error", where="close", error=repr(e))
        alt = (self.case["buyer"].get("alternative") or {}).get("terms") or {}
        c_alt = total_cost(self.case, alt) if alt.get("unit_price") else math.inf
        openings = []
        for events in threads.values():
            sup = supplier_messages(events)
            if sup and sup[0].get("term_sheets"):
                t0 = sup[0]["term_sheets"][0]["terms"]
                if must_haves_safe(self.case, t0, standard_terms(events)):
                    openings.append(total_cost(self.case, t0, standard_terms(events)))
        c_alt = min(c_alt, min(openings) if openings else math.inf)
        best = None
        for sid, events in threads.items():
            sup = supplier_messages(events)
            if not sup or sup[-1].get("status") in ENDED or not sup[-1].get("term_sheets"):
                continue
            sheet = sup[-1]["term_sheets"][0]
            std = standard_terms(events)
            if must_haves_safe(self.case, sheet["terms"], std):
                c = total_cost(self.case, sheet["terms"], std)
                if c < c_alt and (best is None or c < best[0]):
                    best = (c, sid, sheet["term_sheet_id"])
        if best:
            return Decision("accept", best[1], term_sheet_id=best[2], reason=f"{reason}; plain fallback accept C={best[0]:,.0f}")
        live = [s for s, ev in threads.items() if supplier_messages(ev) and supplier_messages(ev)[-1].get("status") not in ENDED]
        return Decision("walk", (live or list(threads))[0], reason=f"{reason}; plain fallback walk")

    def session_result(self):

        state = self.client.session(self.session_id, timeout=CLOSE_TIMEOUT_S)
        if state.get("status") == "ended":
            self.ended = True
        return state.get("result") or {"deal": False, "end_reason": state.get("end_reason")}


def play_case(client, case_id, package, llm=None, case=None, deadline=None, personas=None):
    runner = CaseRunner(client, case_id, package, llm, case, deadline, personas)
    result = runner.play()
    return result, runner.session_id


def play_waves(client, new_runner, waves=5, parallel=9, poll=5.0):
    """The Live Final, after the kit's play_waves (MIT): wait for each wave (403 before the first),
    play its cases in parallel within the wave's cap, stop after `waves` waves have closed. A case
    whose session could not be opened (429: no free slot) or that is still open after an error is
    tried again (a 409 resumes it) until shortly before its cap."""
    started, closed, caps = {}, set(), {}

    def one(case, deadline):
        attempt = 0
        while True:
            try:
                return new_runner(case, deadline)
            except Exception as e:  # noqa: BLE001
                cap = deadline()
                attempt += 1
                pause = min(getattr(e, "retry_after", None) or 2**attempt, OPEN_BACKOFF_MAX_S)
                if cap is None or time.time() + pause > cap - OPEN_MARGIN_S:
                    log.exception("%s failed", case["case_id"])
                    return e
                log.warning("%s: %r: trying again in %.0f s (%.0f s to the cap)", case["case_id"], e, pause,
                            cap - time.time())
                time.sleep(pause)

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        while len(closed) < waves or not all(f.done() for f in started.values()):
            try:
                cases = client.cases("final")
            except ArenaError as e:
                if e.status == 403:
                    cases = []
                elif e.status in (408, 429) or e.status >= 500:
                    log.warning("case list: %s (keep polling)", e)
                    cases = []
                else:
                    raise
            except Exception as e:  # noqa: BLE001 - venue Wi-Fi: keep polling
                log.warning("case list: %s", e)
                cases = []
            for case in cases:
                if not case.get("open"):
                    if case["wave"] not in closed:
                        log.warning("wave %s closed (%d of %d)", case["wave"], len(closed) + 1, waves)
                    closed.add(case["wave"])
                    continue
                if case.get("seconds_left") is not None:
                    caps[case["wave"]] = time.time() + case["seconds_left"]
                if case["case_id"] not in started and case.get("seconds_left"):
                    if case["wave"] not in {c["wave"] for c in cases if c["case_id"] in started}:
                        log.warning("wave %s started: %s", case["wave"], case.get("theme"))
                    started[case["case_id"]] = pool.submit(one, case, functools.partial(caps.get, case["wave"]))
            time.sleep(poll)
    return {cid: f.result() for cid, f in started.items()}


def load_personas(package=None, path=None):
    """The cached profiles. Built from other data than `package` (e.g. before the Sunday release):
    the profiles of suppliers with new release items are dropped, the dossier reads those itself."""
    path = Path(path) if path else ROOT / "cache" / "personas.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    built = (data.pop("_meta", None) or {}).get("data")
    if package is not None and built != data_version(package):
        stale = release_suppliers(package)
        log.warning(
            "personas built for data %s, loaded data %s: %d stale profiles dropped, run `parley prepare`",
            built, data_version(package), len(stale & data.keys()),
        )
        data = {k: v for k, v in data.items() if k not in stale}
    return data


def summarise(result):
    keep = ("deal", "supplier_id", "terms", "c_open", "c_deal", "c_alt", "c_best", "score", "end_reason")
    return {k: result.get(k) for k in keep if k in result}


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
