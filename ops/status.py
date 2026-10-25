# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27", "psutil>=5.9"]
# ///
"""Status view for the person watching the Live Final. Reads only GET /v1/cases?set=final,
GET /v1/sessions?set=final, the supervisor's ops/logs/final-state.json and the local journals.
Prints waves, seconds_left, open cases, cases not picked up, build, restarts and switches."""

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path

import httpx
import psutil
from _common import AGENT, LOGS, child_env

STATE = LOGS / "final-state.json"


def get(http, path):
    try:
        r = http.get(path, params={"set": "final"})
    except httpx.HTTPError as e:
        return None, e.__class__.__name__
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    rows = r.json()
    return (rows.get("sessions", rows) if isinstance(rows, dict) else rows), None


def journal_age(folder, sid):
    hits = [*folder.glob(f"{sid}.jsonl"), *folder.glob(f"*_{sid}.jsonl")]
    if not hits:
        return None
    return time.time() - max(p.stat().st_mtime for p in hits)


def alive(pid):
    try:
        return bool(pid) and psutil.Process(pid).is_running()
    except psutil.Error:
        return False


def render(http, journal):
    out = [f"== {datetime.now():%H:%M:%S}"]
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    if st:
        up = alive(st.get("runner_pid")) if st.get("running") else False
        sup = alive(st.get("pid"))
        out.append(f"build {st.get('active')} {str(st.get('sha'))[:7]}  mode {st.get('mode')}  attempt {st.get('attempt')}  "
                   f"restarts {st.get('restarts')}  switches {len(st.get('switches') or [])}  "
                   f"runner {'UP' if up else 'DOWN'}  supervisor {'UP' if sup else 'DOWN'}")  # fmt: skip
        for sw in st.get("switches") or []:
            out.append(
                f"  SWITCHED {sw['at']}: {sw['from']} -> {sw['to']} ({sw['sha'][:7]})"
            )
        if st.get("exits"):
            e = st["exits"][-1]
            out.append(
                f"  last exit {e['at']}: {e['build']} rc {e['rc']} after {e['ran_s']} s"
            )
        if not sup:
            out.append("  !!! the supervisor is not running: start ops/final again")
    else:
        out.append(f"no supervisor state at {STATE}")
    cases, err = get(http, "/cases")
    if err:
        out.append(f"cases: {'no wave started yet' if err == 'HTTP 403' else err}")
        cases = []
    sessions, serr = get(http, "/sessions")
    sessions = sessions or []
    by_case = {s.get("case_id"): s for s in sessions}
    waves = {}
    for c in cases:
        waves.setdefault(c["wave"], []).append(c)
    closed = [n for n, cs in waves.items() if not any(c.get("open") for c in cs)]
    for n, cs in sorted(waves.items()):
        if any(c.get("open") for c in cs):
            left = next((c.get("seconds_left") for c in cs if c.get("open")), None)
            out.append(
                f"wave {n} '{cs[0].get('theme')}' OPEN, {left} s left, {len(cs)} cases"
            )
            for c in cs:
                s = by_case.get(c["case_id"])
                if not s:
                    out.append(f"  {c['case_id']:<16} NOT PICKED UP")
                    continue
                age = journal_age(journal, s.get("session_id"))
                age = "-" if age is None else f"{age:.0f} s"
                out.append(f"  {c['case_id']:<16} {s.get('status'):<6} {str(s.get('end_reason') or ''):<12} "
                           f"rounds {s.get('rounds')}  journal {age} ago")  # fmt: skip
    out.append(f"waves closed: {','.join(map(str, sorted(closed))) or '-'}")
    if serr:
        out.append(f"sessions: {serr}")
    else:
        opened = [s for s in sessions if s.get("status") == "open"]
        ended = [s for s in sessions if s.get("status") != "open"]
        capped = [s.get("case_id") for s in ended if s.get("end_reason") == "wave_cap"]
        out.append(f"sessions: {len(opened)} open, {len(ended)} ended ({sum(1 for s in ended if s.get('deal'))} deals)"
                   + (f"  CAPPED: {', '.join(capped)}" if capped else ""))  # fmt: skip
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--every", type=float, default=15)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--journal-dir", default=str(AGENT / "runs" / "journal"))
    a = ap.parse_args()
    env = child_env()
    key = env.get("ARENA_API_KEY")
    http = httpx.Client(base_url=env["ARENA_URL"].rstrip("/") + "/v1", headers={"X-API-Key": key} if key else {},
                        timeout=20)  # fmt: skip
    journal = Path(a.journal_dir)
    while True:
        text = render(http, journal)
        if not a.once and os.name == "nt":
            os.system("cls")
        elif not a.once:
            print("\033[2J\033[H", end="")
        print(text, flush=True)
        if a.once:
            return
        time.sleep(a.every)


if __name__ == "__main__":
    main()
