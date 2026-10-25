# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27", "psutil>=5.9"]
# ///
"""Local Live Final rehearsal: arena-sim --final-rehearsal (403 wait, two capped waves) behind the
fault proxy, a fresh sim team, Parley through ops/final.py from clean checkouts (candidate + fallback),
then a summary and a gate. Free: no arena key, no quota, --no-llm by default.

--faults is the dress rehearsal: kill the runner mid-wave 1, a crash loop in wave 2 (three kills,
which must switch to the fallback build), 60-120 s reply delays, 429 bursts, slow attachments, a
long-Retry-After 429 on each case's first session start and a Sunday release that is not out yet. Gate: every case got a session, none was left open at the cap, every kill was
followed by a runner that picked up all open sessions within --pickup-s, the crash loop switched.
"""

import argparse
import json
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import psutil
from _common import (
    AGENT,
    KIT,
    LOGS,
    OPS,
    STARTER,
    child_env,
    kill_tree,
    stamp,
    today_dir,
)
from fault_proxy import Faults, serve
from final import DEFAULT_ROOT

STATE = LOGS / "final-state.json"


def port_busy(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def data_dir():
    for p in (AGENT / "data" / "package", KIT / "data"):
        if (p / "facts.json").is_file():
            return p
    sys.exit("no data package found (agent/data/package or the kit's data/)")


def runners(root_proc):
    """The parley processes (uv wrapper and python) under the supervisor."""
    try:
        kids = psutil.Process(root_proc.pid).children(recursive=True)
    except psutil.Error:
        return []
    out = []
    for p in kids:
        try:
            cmd = " ".join(p.cmdline()).replace("\\", "/").lower()
        except psutil.Error:
            continue
        if "parley" in cmd and " run" in cmd and "final.py" not in cmd:
            out.append(p)
    return out


def state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class Monitor(threading.Thread):
    """Talks to the sim directly (not through the faults): wave close times and when each session ended."""

    def __init__(self, sim_url, key):
        super().__init__(daemon=True)
        self.http = httpx.Client(
            base_url=f"{sim_url}/v1", headers={"X-API-Key": key}, timeout=10
        )
        self.closes, self.opens, self.ended, self.stop = {}, {}, {}, threading.Event()

    def cases(self):
        r = self.http.get("/cases", params={"set": "final"})
        return r.json() if r.status_code == 200 else []

    def sessions(self):
        r = self.http.get("/sessions", params={"set": "final"})
        return r.json() if r.status_code == 200 else []

    def run(self):
        while not self.stop.is_set():
            try:
                t = time.time()
                for c in self.cases():
                    if c.get("open") and c.get("seconds_left") is not None:
                        self.closes.setdefault(c["wave"], t + c["seconds_left"])
                        self.opens.setdefault(c["wave"], t)
                for s in self.sessions():
                    if s.get("status") != "open":
                        self.ended.setdefault(s["session_id"], t)
            except httpx.HTTPError:
                pass
            self.stop.wait(2)


def chaos(final_proc, mon, cap, plan, notes):
    """plan: [(wave, kills)]. Kills land once 35 % of the wave has run; in a crash loop each further
    kill comes 4 s after the previous runner came up."""
    for wave, kills in plan:
        while final_proc.poll() is None:
            if wave in mon.closes and mon.closes[wave] - time.time() <= cap * 0.65:
                break
            time.sleep(1)
        for k in range(kills):
            if final_proc.poll() is not None:
                return
            t_wait = time.time()
            while final_proc.poll() is None and time.time() - t_wait < 90:
                procs = runners(final_proc)
                if procs and time.time() - min(p.create_time() for p in procs) >= (
                    4 if k else 0
                ):
                    break
                time.sleep(0.5)
            procs = runners(final_proc)
            if not procs:
                notes.append({"wave": wave, "error": "no runner process to kill"})
                continue
            open_sids = [
                s["session_id"] for s in mon.sessions() if s.get("status") == "open"
            ]
            t_kill = time.time()
            for p in procs:
                try:
                    p.kill()
                except psutil.Error:
                    pass
            row = {"wave": wave, "kill": k + 1, "of": kills, "at": t_kill, "build": state().get("active"),
                   "open_sessions": open_sids, "left_s": round(mon.closes.get(wave, t_kill) - t_kill)}  # fmt: skip
            notes.append(row)
            print(f"[rehearsal] kill {k + 1}/{kills} in wave {wave}: runner of build {row['build']}, "
                  f"{len(open_sids)} open sessions, {row['left_s']} s left", flush=True)  # fmt: skip
            t_new = time.time()
            while final_proc.poll() is None and time.time() - t_new < 120:
                new = [p for p in runners(final_proc) if p.create_time() > t_kill]
                if new:
                    row["restart_at"] = min(p.create_time() for p in new)
                    break
                time.sleep(0.25)
            time.sleep(1)
            row["build_after"] = state().get("active")


def pickups(notes, events, ended, limit):
    """For each kill whose successor ran: when the new runner first touched each session open at the kill."""
    out = []
    for i, n in enumerate(notes):
        if "restart_at" not in n:
            continue
        nxt = notes[i + 1]["at"] if i + 1 < len(notes) else float("inf")
        survived = nxt - n["restart_at"]
        rows = []
        for sid in n["open_sessions"]:
            if ended.get(sid, float("inf")) < n["restart_at"]:
                continue
            hit = next(
                (
                    e["t"]
                    for e in events
                    if e["t"] > n["restart_at"] and f"/sessions/{sid}" in e["path"]
                ),
                None,
            )
            rows.append(
                {
                    "sid": sid,
                    "after_s": None if hit is None else round(hit - n["restart_at"], 1),
                }
            )
        judged = survived > limit + 5
        bad = (
            [r for r in rows if r["after_s"] is None or r["after_s"] > limit]
            if judged
            else []
        )
        out.append({"wave": n["wave"], "kill": n["kill"], "build_after": n.get("build_after"), "runner_survived_s": round(min(survived, 9999)),
                    "judged": judged, "sessions": rows, "late_or_missing": bad})  # fmt: skip
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--port", type=int, default=8000, help="sim port; the proxy listens on port + 1"
    )
    ap.add_argument(
        "--with-llm", action="store_true", help="use the models (costs tokens)"
    )
    ap.add_argument(
        "--crash-test", action="store_true", help="kill the runner once during wave 1"
    )
    ap.add_argument(
        "--faults", action="store_true", help="the dress rehearsal (see above)"
    )
    ap.add_argument(
        "--wait",
        type=float,
        default=60,
        help="seconds until wave 1 (sim --rehearsal-wait)",
    )
    ap.add_argument(
        "--cap",
        type=float,
        default=None,
        help="seconds per wave (default 240, with --faults 300)",
    )
    ap.add_argument("--break-s", type=float, default=30, help="seconds between waves")
    ap.add_argument(
        "--delay-share",
        type=float,
        default=0.2,
        help="--faults: share of messages held 60-120 s",
    )
    ap.add_argument(
        "--burst-every",
        type=float,
        default=75,
        help="--faults: seconds between 429 bursts (0 = none)",
    )
    ap.add_argument(
        "--burst-len",
        type=float,
        default=6,
        help="--faults: seconds each 429 burst lasts",
    )
    ap.add_argument(
        "--doc-share",
        type=float,
        default=0.3,
        help="--faults: share of attachment downloads held --doc-delay s",
    )
    ap.add_argument("--doc-delay", type=float, default=40, help="--faults: seconds a slow attachment is held")
    ap.add_argument(
        "--open-429",
        type=int,
        default=1,
        help="--faults: session starts per case refused with 429 and a long Retry-After",
    )
    ap.add_argument("--open-429-after", type=int, default=30, help="--faults: that Retry-After in seconds")
    ap.add_argument(
        "--release",
        choices=["missing", "public", "error"],
        default="missing",
        help="what GET /v1/data says",
    )
    ap.add_argument(
        "--pickup-s",
        type=float,
        default=10,
        help="gate: a restarted runner touches each open session within this",
    )
    ap.add_argument("--rc", default="HEAD")
    ap.add_argument("--fallback", default="F1R")
    ap.add_argument("--fallback2", default="F1")
    ap.add_argument(
        "--root", default=None, help="checkout folder (default <final root>/rehearsal)"
    )
    ap.add_argument(
        "--in-place",
        action="store_true",
        help="run this repo's agent/ instead of checkouts",
    )
    ap.add_argument(
        "--reply-delay",
        type=float,
        default=0,
        help="median seconds every supplier reply takes (ops/slow_sim.py; default 0 = instant)",
    )
    ap.add_argument("--reply-sigma", type=float, default=0.45, help="spread of --reply-delay")
    a = ap.parse_args()
    cap = a.cap or (300 if a.faults else 240)
    for port in (a.port, a.port + 1):
        if port_busy(port):
            sys.exit(
                f"port {port} is in use on 127.0.0.1: stop that service or pass --port <free port>"
            )
    sim_url, url = f"http://127.0.0.1:{a.port}", f"http://127.0.0.1:{a.port + 1}"
    out = today_dir() / f"rehearsal-{stamp()}"
    out.mkdir()
    uv = shutil.which("uv") or sys.exit("uv not found")
    serve_cmd = [uv, "run", "--extra", "sim", "python", str(OPS / "slow_sim.py")] if a.reply_delay > 0 else [uv, "run", "--extra", "sim", "arena-sim"]
    sim_cmd = [*serve_cmd, "serve", "--final-rehearsal", "--data", str(data_dir()), "--port", str(a.port),
               "--rehearsal-wait", str(a.wait), "--rehearsal-cap", str(cap), "--rehearsal-break", str(a.break_s)]  # fmt: skip
    sim_log = (out / "arena-sim.log").open("w", encoding="utf-8")
    sim = subprocess.Popen(
        sim_cmd,
        cwd=STARTER,
        env=child_env({"SIM_REPLY_DELAY": str(a.reply_delay), "SIM_REPLY_SIGMA": str(a.reply_sigma)}),
        stdout=sim_log,
        stderr=subprocess.STDOUT,
    )
    faults = Faults(sim_url, delay_share=a.delay_share if a.faults else 0, burst_every=a.burst_every if a.faults else 0,
                    burst_len=a.burst_len, data=a.release, log_path=out / "proxy.jsonl",
                    doc_share=a.doc_share if a.faults else 0, doc_delay_s=a.doc_delay,
                    open_429=a.open_429 if a.faults else 0, open_429_after=a.open_429_after)  # fmt: skip
    proxy = serve(faults, a.port + 1)
    notes, mon = [], None
    try:
        for _ in range(120):
            try:
                if httpx.get(f"{sim_url}/health", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if sim.poll() is not None:
                sys.exit(
                    f"arena-sim exited ({sim.returncode}); see {out / 'arena-sim.log'}"
                )
            time.sleep(1)
        else:
            sys.exit("arena-sim did not come up")
        r = httpx.post(
            f"{sim_url}/v1/register",
            json={"team_name": f"Rehearsal {stamp()}", "event_code": "sim"},
            timeout=10,
        )
        r.raise_for_status()
        key = r.json()["api_key"]
        faults.t0 = time.time()
        print(f"[rehearsal] arena-sim at {sim_url}, fault proxy at {url} (faults {'on' if a.faults else 'off'}, "
              f"release {a.release}), logs in {out}", flush=True)  # fmt: skip
        mon = Monitor(sim_url, key)
        mon.start()
        env = child_env({"ARENA_URL": url, "ARENA_API_KEY": key})
        journal = out / "journal"
        cmd = [uv, "run", "--script", str(OPS / "final.py"), "--waves", "2", "--skip-loop-check", "--label", "rehearsal",
               "--status-every", "15", "--journal-dir", str(journal), "--data", str(data_dir())]  # fmt: skip
        if a.in_place:
            cmd.append("--in-place")
        else:
            root = a.root or str(DEFAULT_ROOT / "rehearsal")
            cmd += [
                "--rc",
                a.rc,
                "--fallback",
                a.fallback,
                "--fallback2",
                a.fallback2,
                "--root",
                root,
            ]
        if a.with_llm:
            cmd.append("--with-llm")
        t0 = time.time()
        final_log = (out / "final.out").open("w", encoding="utf-8")
        final = subprocess.Popen(cmd, cwd=OPS, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 encoding="utf-8", errors="replace", bufsize=1)  # fmt: skip

        def tee():
            for line in iter(final.stdout.readline, ""):
                print(line, end="", flush=True)
                final_log.write(line)
                final_log.flush()

        reader = threading.Thread(target=tee, daemon=True)
        reader.start()
        plan = [(1, 1), (2, 3)] if a.faults else [(1, 1)] if a.crash_test else []
        if plan:
            threading.Thread(
                target=chaos, args=(final, mon, cap, plan, notes), daemon=True
            ).start()
        rc = final.wait()
        reader.join(timeout=5)
        final_log.close()
        took = time.time() - t0
        time.sleep(3)
        mon.stop.set()
        http = httpx.Client(
            base_url=f"{sim_url}/v1", headers={"X-API-Key": key}, timeout=30
        )
        rows = http.get("/sessions", params={"set": "final"}).json()
        details = [http.get(f"/sessions/{s['session_id']}").json() for s in rows]
        announced = sorted(sim_cases_final(out / "arena-sim.log"))
        sessions = []
        for d in details:
            res = d.get("result") or {}
            wave = (
                int(d["case_id"].split(":R", 1)[1][0]) if ":R" in d["case_id"] else None
            )
            ended = mon.ended.get(d["session_id"])
            left = (
                round(mon.closes[wave] - ended)
                if wave in mon.closes and ended
                else None
            )
            sessions.append({"case_id": d["case_id"], "session_id": d["session_id"], "status": d.get("status"),
                             "end_reason": d.get("end_reason"), "rounds": d.get("rounds"), "left_at_end_s": left,
                             **{k: res.get(k) for k in ("deal", "score", "c_open", "c_deal", "c_best", "c_alt")}})  # fmt: skip
        events = sorted(faults.events, key=lambda e: e["t"])
        picks = pickups(notes, events, mon.ended, a.pickup_s)
        st = state()
        faults_seen = {
            "delayed_messages": sum(1 for e in events if e.get("fault") == "delay"),
            "burst_429": sum(1 for e in events if e.get("fault") == "burst"),
            "data_calls": sum(1 for e in events if e.get("fault") == "data"),
            "slow_attachments": sum(1 for e in events if e.get("fault") == "doc_delay"),
            "open_429": sum(1 for e in events if e.get("fault") == "open_429"),
            "upstream_429": sum(
                1 for e in events if e.get("status") == 429 and not e.get("fault")
            ),
        }
        text = (out / "final.out").read_text(encoding="utf-8", errors="replace")
        gate = {}
        have = {s["case_id"] for s in sessions}
        gate["every case got a session"] = not [
            c for c in announced if c not in have
        ] and bool(announced)
        gate["no case open at the cap"] = not [
            s
            for s in sessions
            if s["status"] == "open" or s["end_reason"] == "wave_cap"
        ]
        gate["supervisor finished (rc 0)"] = rc == 0
        first = (st.get("builds") or [{}])[0].get("name")
        own = [e for e in st.get("exits") or [] if e.get("build") == first]
        gate["no crash loop: the first build exits only on the injected kills"] = len(own) <= sum(k for _, k in plan)
        if a.release == "missing":
            gate["ran without the Sunday release"] = (
                "+ Sunday release" not in text and "data:" in text
            )
        if plan:
            judged = [p for p in picks if p["judged"]]
            gate[f"restarts picked up all open sessions within {a.pickup_s:.0f} s"] = (
                bool(judged) and not any(p["late_or_missing"] for p in judged)
            )
        if a.faults:
            gate["crash loop switched to the fallback"] = any(
                s.get("from") == first and s.get("to") in ("safe", "fallback") for s in st.get("switches") or []
            )
        late = [
            s["case_id"]
            for s in sessions
            if s["left_at_end_s"] is not None and s["left_at_end_s"] < 90
        ]
        scores = [s["score"] for s in sessions if s.get("score") is not None]
        summary = {
            "rc": rc, "seconds": round(took), "llm": a.with_llm, "faults": a.faults, "release": a.release, "cap_s": cap,
            "announced": announced, "sessions": sessions, "mean_score": round(sum(scores) / len(scores), 3) if scores else None,
            "kills": notes, "pickups": picks, "switches": st.get("switches"), "restarts": st.get("restarts"),
            "exits": st.get("exits"), "faults_seen": faults_seen, "closed_with_under_90s_left": late, "gate": gate,
        }  # fmt: skip
        (out / "summary.json").write_text(
            json.dumps(summary, indent=1), encoding="utf-8"
        )
        print("\n[rehearsal] summary")
        for s in sorted(sessions, key=lambda s: s["case_id"]):
            print(f"  {s['case_id']:<14} {s['status']:<6} {str(s['end_reason']):<12} deal={s['deal']}  "
                  f"score={s['score'] if s['score'] is None else round(s['score'], 3)}  rounds={s['rounds']}  "
                  f"left at end={s['left_at_end_s']} s")  # fmt: skip
        print(f"  runner rc {rc}, {took:.0f} s, mean score {summary['mean_score']}, restarts {st.get('restarts')}, "
              f"switches {[(s['from'], s['to']) for s in st.get('switches') or []]}")  # fmt: skip
        print(f"  faults: {faults_seen}")
        for p in picks:
            print(f"  pickup after kill {p['kill']} in wave {p['wave']} (build {p['build_after']}, survived {p['runner_survived_s']} s): "
                  + (", ".join(f"{r['sid']} {r['after_s']} s" for r in p["sessions"]) or "no open sessions")
                  + ("" if p["judged"] else "  (killed again: not judged)"))  # fmt: skip
        if late:
            print(f"  WARN closed with < 90 s left: {', '.join(late)}")
        other = [e for e in st.get("exits") or [] if e.get("build") != first]
        if other:
            print(f"  WARN exits of the fallback builds: {[(e['build'], e['rc'], e['ran_s']) for e in other]}")
        print("\n[rehearsal] gate")
        for k, v in gate.items():
            print(f"  [{'PASS' if v else 'FAIL'}] {k}")
        print(f"  details: {out / 'summary.json'}")
        sys.exit(0 if all(gate.values()) else 1)
    finally:
        if mon:
            mon.stop.set()
        proxy.shutdown()
        kill_tree(sim)
        sim_log.close()


def sim_cases_final(sim_log_path):
    import re

    text = (
        sim_log_path.read_text(encoding="utf-8", errors="replace")
        if sim_log_path.is_file()
        else ""
    )
    ids = set()
    for line in text.splitlines():
        if "starts:" in line:
            ids |= set(re.findall(r"case:R\d-\w+", line))
    return ids


if __name__ == "__main__":
    main()
