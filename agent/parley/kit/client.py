"""Thin HTTP client for the arena API. Every method returns the JSON the server sends."""

import os
import threading
import time
import uuid

import httpx

# Venue Wi-Fi drops. Retrying with the same move id makes the server return the reply it already
# gave instead of playing the move twice, so retries are safe even mid-negotiation.
MOVE_ID_HEADER = "X-Move-Id"
RETRY_STATUS = {429, 500, 502, 503, 504}
CALLS_PER_SECOND = 9  # the arena allows 10 per key; stay just below, across all threads
# A supplier's reply can take up to about a minute (its time does not count against your 60 s),
# and a call may wait for another call on the same session first: wait long enough for both.
TIMEOUT_S = 180.0
# Practice has a daily quota per team: every practice session start carries what is left in this
# header, and GET /v1/me tells where the team stands.
REMAINING_HEADER = "X-Practice-Remaining"
ME_PATH = "/me"
# A 429 with a short Retry-After is the per-second rate limit: wait and retry. Any other 429 (the
# practice quota, too many open sessions) does not go away by retrying.
MAX_RETRY_AFTER_S = 5


# A call that cannot finish before the caller's deadline is given up with this status.
DEADLINE_STATUS = 408


class ArenaError(Exception):
    def __init__(self, status: int, detail, retry_after=None):
        super().__init__(f"HTTP {status}: {detail}")
        self.status, self.detail, self.retry_after = status, detail, retry_after


class ArenaClient:
    def __init__(
        self,
        base_url=None,
        api_key=None,
        *,
        retries=3,
        transport=None,
        timeout=TIMEOUT_S,
        rate=CALLS_PER_SECOND,
    ):
        base_url = (base_url or os.environ.get("ARENA_URL", "")).rstrip("/")
        if not base_url:
            raise ValueError("set ARENA_URL (e.g. https://…) or pass base_url")
        key = api_key or os.environ.get("ARENA_API_KEY")
        headers = {"X-API-Key": key} if key else {}
        self.retries, self.rate, self._lock, self._next = retries, rate, threading.Lock(), 0.0
        self.practice_remaining = None  # from the last practice reply that said so
        self.timeout = timeout
        self.http = httpx.Client(base_url=f"{base_url}/v1", headers=headers, timeout=timeout, transport=transport)

    def _pace(self):
        with self._lock:
            now = time.monotonic()
            wait, self._next = self._next - now, max(now, self._next) + 1 / self.rate
        if wait > 0:
            time.sleep(wait)

    def _call(self, method, path, body=None, params=None, raw=False, timeout=None, deadline=None):
        """`timeout`: seconds per attempt (default: the client's). `deadline`: a time.time() after which
        no attempt starts and no attempt waits beyond; the call then fails with DEADLINE_STATUS."""
        headers = {MOVE_ID_HEADER: uuid.uuid4().hex} if method == "POST" else {}
        r, attempt, failures = None, 0, 0
        while True:
            per = timeout or self.timeout
            if deadline is not None:
                left = deadline - time.time()
                if left <= 0.5:
                    if r is not None:
                        break
                    raise ArenaError(DEADLINE_STATUS, f"deadline reached before {method} {path}")
                per = min(per, left)
            self._pace()
            try:
                r = self.http.request(method, path, json=body, params=params, headers=headers, timeout=per)
            except httpx.TransportError:
                failures += 1
                if failures > self.retries or (deadline is not None and time.time() >= deadline - 0.5):
                    raise
                wait = None
            else:
                wait = r.headers.get("Retry-After", "")
                short = wait.isdigit() and int(wait) <= MAX_RETRY_AFTER_S
                if r.status_code not in RETRY_STATUS or (r.status_code == 429 and not short):
                    break
                # With a deadline, the per-second rate limit (a short 429, e.g. a burst) is waited out
                # until the deadline; every other failure counts against `retries`.
                if not (deadline is not None and r.status_code == 429):
                    failures += 1
                if failures > self.retries:
                    break
            pause = float(wait) if wait and wait.isdigit() else 0.5 * 2**min(attempt, 4)
            attempt += 1
            if deadline is not None and time.time() + pause >= deadline - 0.5:
                break
            time.sleep(pause)
        if r is None:
            raise ArenaError(DEADLINE_STATUS, f"no reply to {method} {path} before the deadline")
        if (left := r.headers.get(REMAINING_HEADER, "")).isdigit():
            self.practice_remaining = int(left)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except (ValueError, AttributeError):
                detail = r.text
            after = r.headers.get("Retry-After", "")
            raise ArenaError(r.status_code, detail, float(after) if after.replace(".", "", 1).isdigit() else None)
        return r.content if raw else r.json()

    def register(self, team_name, event_code):
        """arena-sim only: the simulator hands out its own keys. On the arena, the organisers
        give each team its key."""
        return self._call("POST", "/register", {"team_name": team_name, "event_code": event_code})

    def me(self):
        """Your team: practice sessions used and left, when the quota resets, best score per case."""
        return self._call("GET", ME_PATH)

    def cases(self, set_="practice"):
        return self._call("GET", "/cases", params={"set": set_})

    def open_session(self, case_id, **kw):
        return self._call("POST", "/sessions", {"case_id": case_id}, **kw)

    def send_message(self, session_id, supplier_id, message, **kw):
        """Your e-mail to one supplier; returns its reply (or the result, if `action` closes).
        `timeout`/`deadline` as in `_call`."""
        path = f"/sessions/{session_id}/threads/{supplier_id}/messages"
        return self._call("POST", path, {"message": message}, **kw)

    def thread(self, session_id, supplier_id, **kw):
        return self._call("GET", f"/sessions/{session_id}/threads/{supplier_id}", **kw)

    def attachment(self, session_id, attachment_id, **kw):
        """The bytes of a document a supplier attached."""
        return self._call("GET", f"/sessions/{session_id}/attachments/{attachment_id}", raw=True, **kw)

    def backoffice(self, session_id):
        return self._call("GET", f"/sessions/{session_id}/backoffice")

    def move(self, session_id, supplier_id, move):
        body = {"supplier_id": supplier_id, "move": move}
        return self._call("POST", f"/sessions/{session_id}/moves", body)

    def accept(self, session_id, offer_id):
        return self._call("POST", f"/sessions/{session_id}/accept", {"offer_id": offer_id})

    def walk_away(self, session_id):
        return self._call("POST", f"/sessions/{session_id}/walk_away")

    def session(self, session_id, **kw):
        return self._call("GET", f"/sessions/{session_id}", **kw)

    def sessions(self, set_="practice"):
        return self._call("GET", "/sessions", params={"set": set_})

    def leaderboard(self, set_="practice"):
        return self._call("GET", "/leaderboard", params={"set": set_})

    def downloads(self):
        """The data you can download: the package, and the Sunday release once it is public."""
        return self._call("GET", "/data")

    def download(self, name):
        """A data download (zip bytes), e.g. "package" or "release-sunday"."""
        return self._call("GET", f"/data/{name}.zip", raw=True)
