# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27"]
# ///
"""Fault-injecting HTTP proxy in front of arena-sim for the rehearsal: slow supplier replies
(a share of POST .../messages held 60-120 s before they reach the sim), 429 bursts with
Retry-After: 1 on every /v1 call, slow attachments (a share of GET .../attachments/... held
--doc-delay s), a long-Retry-After 429 ("no free session slot", Retry-After --open-429-after) on the
first --open-429 POST /v1/sessions of each case, and GET /v1/data as the arena serves it (Sunday
release missing, public, or the endpoint failing). Logs every request with its time and status.

uv run --script ops/fault_proxy.py --target http://127.0.0.1:8000 --port 8001 [--delay-share 0.2]
"""

import argparse
import json
import random
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

MESSAGES = re.compile(r"^/v1/sessions/[^/]+/threads/[^/]+/messages$")
ATTACHMENT = re.compile(r"^/v1/sessions/[^/]+/attachments/[^/]+$")
FORWARD = ("x-api-key", "x-move-id", "content-type")
BACK = ("content-type", "retry-after", "x-practice-remaining")


class Faults:
    def __init__(self, target, delay_share=0.2, delay_s=(60, 120), burst_every=75, burst_len=6, burst_after=30,
                 data="missing", data_error_s=0, seed=7, log_path=None, doc_share=0.0, doc_delay_s=40,
                 open_429=0, open_429_after=30):  # fmt: skip
        self.target = target.rstrip("/")
        self.delay_share, self.delay_s = delay_share, delay_s
        self.burst_every, self.burst_len, self.burst_after = (
            burst_every,
            burst_len,
            burst_after,
        )
        self.data, self.data_error_s = data, data_error_s
        self.rng, self.lock = random.Random(seed), threading.Lock()
        self.t0 = time.time()
        self.events, self.log_path = [], log_path
        self.http = httpx.Client(timeout=300)
        self.enabled = True
        self.doc_share, self.doc_delay_s = doc_share, doc_delay_s
        self.open_429, self.open_429_after, self.opens = open_429, open_429_after, {}

    def doc_delay(self, method, path):
        if method != "GET" or not ATTACHMENT.match(path) or not self.enabled or not self.doc_share:
            return 0.0
        with self.lock:
            return self.doc_delay_s if self.rng.random() < self.doc_share else 0.0

    def refuse_open(self, method, path, body):
        """The first `open_429` session starts of each case get a 429 with a long Retry-After."""
        if method != "POST" or path != "/v1/sessions" or not self.enabled or not self.open_429:
            return False
        try:
            case_id = json.loads(body or b"{}").get("case_id")
        except ValueError:
            return False
        with self.lock:
            n = self.opens[case_id] = self.opens.get(case_id, 0) + 1
        return n <= self.open_429

    def bursting(self, t):
        if not self.burst_every or not self.enabled:
            return False
        x = t - self.t0 - self.burst_after
        return x >= 0 and x % self.burst_every < self.burst_len

    def delay_for(self, method, path):
        if method != "POST" or not MESSAGES.match(path) or not self.enabled:
            return 0.0
        with self.lock:
            if self.rng.random() >= self.delay_share:
                return 0.0
            return self.rng.uniform(*self.delay_s)

    def record(self, **row):
        row["t"] = round(time.time(), 3)
        with self.lock:
            self.events.append(row)
            if self.log_path:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row) + "\n")

    def data_reply(self):
        if self.data == "error" or (
            self.data_error_s and time.time() - self.t0 < self.data_error_s
        ):
            return 503, {"detail": "service unavailable (injected)"}
        release = {"public": False, "at": "2026-10-25T07:00:00+00:00"}
        if self.data == "public":
            release = {
                "public": True,
                "at": "2026-10-25T07:00:00+00:00",
                "url": "/v1/data/release-sunday.zip",
            }
        return 200, {
            "package": {"url": "/v1/data/package.zip"},
            "release-sunday": release,
        }


def handler(faults):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def reply(self, status, body, headers=None):
            raw = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            for k, v in (headers or {"content-type": "application/json"}).items():
                self.send_header(k, v)
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def handle_any(self):
            t = time.time()
            path = self.path.split("?", 1)[0]
            n = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(n) if n else b""
            if path == "/v1/data":
                status, out = faults.data_reply()
                faults.record(
                    method=self.command, path=self.path, status=status, fault="data"
                )
                return self.reply(status, out)
            if (
                path.startswith("/v1/")
                and path != "/v1/register"
                and faults.bursting(t)
            ):
                faults.record(
                    method=self.command, path=self.path, status=429, fault="burst"
                )
                return self.reply(429, {"detail": "more than 10 calls per second"},
                                  {"content-type": "application/json", "retry-after": "1"})  # fmt: skip
            if faults.refuse_open(self.command, path, body):
                faults.record(method=self.command, path=self.path, status=429, fault="open_429")
                return self.reply(429, {"detail": "too many open sessions (injected)"},
                                  {"content-type": "application/json", "retry-after": str(faults.open_429_after)})  # fmt: skip
            delay = faults.delay_for(self.command, path) or faults.doc_delay(self.command, path)
            if delay:
                faults.record(
                    method=self.command,
                    path=self.path,
                    status=None,
                    fault="doc_delay" if ATTACHMENT.match(path) else "delay",
                    delay=round(delay, 1),
                )
                time.sleep(delay)
            headers = {k: v for k, v in self.headers.items() if k.lower() in FORWARD}
            try:
                r = faults.http.request(
                    self.command,
                    faults.target + self.path,
                    content=body,
                    headers=headers,
                )
            except httpx.HTTPError as e:
                faults.record(
                    method=self.command,
                    path=self.path,
                    status=502,
                    fault="upstream",
                    error=str(e),
                )
                return self.reply(502, {"detail": f"proxy: {e.__class__.__name__}"})
            faults.record(method=self.command, path=self.path, status=r.status_code, delay=round(delay, 1) or None,
                          took=round(time.time() - t, 2))  # fmt: skip
            back = {k: v for k, v in r.headers.items() if k.lower() in BACK}
            try:
                self.reply(r.status_code, r.content, back)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        do_GET = do_POST = do_PUT = do_DELETE = handle_any

    return H


def serve(faults, port, host="127.0.0.1"):
    srv = ThreadingHTTPServer((host, port), handler(faults))
    srv.daemon_threads = True
    srv.handle_error = lambda *_: None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--target", required=True)
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--delay-share", type=float, default=0.2)
    ap.add_argument("--delay-min", type=float, default=60)
    ap.add_argument("--delay-max", type=float, default=120)
    ap.add_argument("--burst-every", type=float, default=75, help="0 = no 429 bursts")
    ap.add_argument("--burst-len", type=float, default=6)
    ap.add_argument("--data", choices=["missing", "public", "error"], default="missing")
    ap.add_argument("--doc-share", type=float, default=0.0, help="share of attachment downloads held --doc-delay s")
    ap.add_argument("--doc-delay", type=float, default=40)
    ap.add_argument("--open-429", type=int, default=0, help="session starts per case refused with a long Retry-After")
    ap.add_argument("--open-429-after", type=int, default=30)
    ap.add_argument("--log")
    a = ap.parse_args()
    f = Faults(a.target, a.delay_share, (a.delay_min, a.delay_max), a.burst_every, a.burst_len, data=a.data,
               log_path=a.log, doc_share=a.doc_share, doc_delay_s=a.doc_delay, open_429=a.open_429,
               open_429_after=a.open_429_after)  # fmt: skip
    serve(f, a.port)
    print(f"fault proxy on http://127.0.0.1:{a.port} -> {a.target}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
