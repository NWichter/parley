# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27", "psutil>=5.9"]
# ///
"""Supervised Live Final runner with a fallback build.

Runs `parley run --set final --no-llm` from clean checkouts under <root>/<name> (`--root` or PARLEY_FINAL_ROOT; each with its
own locked venv): the candidate (`--rc`, a git ref or a path to a repo such as the submission clone),
then the same candidate with the new flags off (`safe`, SAFE_ENV; `--no-safe` skips it), then the fallback (`--fallback`, tag F1R) and a second fallback (`--fallback2`, tag F1). A crashed runner
restarts after 3 s and picks up its open sessions via the 409; three exits within 120 s switch to the
next build. One instance at a time (lock file), refuses to start while practice loops or open
practice/sandbox sessions exist, logs to ops/logs/<date>/, writes ops/logs/final-state.json for
ops/status.py, and stops once the requested number of waves has closed. `--prepare-only` builds the
checkouts and exits.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import httpx
import psutil
from _common import (
    AGENT,
    KIT,
    LOGS,
    ROOT,
    agent_processes,
    child_env,
    kill_tree,
    stamp,
    today_dir,
    tree_digest,
)

LOCK = LOGS / "final.lock"
STATE = LOGS / "final-state.json"
GRACE_AFTER_LAST_WAVE_S = 180
DEFAULT_ROOT = (
    Path(Path.home().anchor) if os.name == "nt" else Path.home()
) / "parley-final"
STALE_DATA = "Sunday release is public but not loaded"
# parley's exit code for a missing or unreadable data package: every restart would fail the same way.
EXIT_NO_DATA = 78


def now():
    return datetime.now().strftime("%H:%M:%S")


def say(msg, log=None):
    line = f"[ops {now()}] {msg}"
    print(line, flush=True)
    if log:
        log.write(line + "\n")
        log.flush()


def shout(msg, log=None):
    bar = "!" * 78
    for line in (bar, f"!!! {msg}", bar):
        say(line, log)


def take_lock():
    LOGS.mkdir(parents=True, exist_ok=True)
    if LOCK.exists():
        try:
            info = json.loads(LOCK.read_text(encoding="utf-8"))
            pid = int(info.get("pid", 0))
        except (ValueError, OSError):
            info, pid = {}, 0
        alive = False
        if pid and psutil.pid_exists(pid):
            try:
                alive = "final.py" in " ".join(psutil.Process(pid).cmdline())
            except psutil.Error:
                alive = False
        if alive:
            sys.exit(
                f"another final runner is active (pid {pid}, since {info.get('started')}): {LOCK}"
            )
        LOCK.unlink(missing_ok=True)
    fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(
            {
                "pid": os.getpid(),
                "started": datetime.now().isoformat(timespec="seconds"),
            },
            f,
        )


def release_lock():
    try:
        if json.loads(LOCK.read_text(encoding="utf-8")).get("pid") == os.getpid():
            LOCK.unlink()
    except (OSError, ValueError):
        pass


def git(*args, cwd=ROOT, check=True):
    p = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace")  # fmt: skip
    if check and p.returncode:
        sys.exit(f"git {' '.join(args)} (in {cwd}) failed: {p.stderr.strip()}")
    return p.stdout.strip()


def data_dir(explicit=None):
    if explicit:
        p = Path(explicit).resolve()
        if not (p / "facts.json").is_file():
            sys.exit(f"--data {p}: no facts.json")
        return p
    for p in (AGENT / "data" / "package", KIT / "data"):
        if (p / "facts.json").is_file():
            return p.resolve()
    sys.exit(
        "no data package found (agent/data/package or the kit's data/): run ops/sunday-data first"
    )


def release_manifest(pkg):
    """The release folder next to the package and the checksum ops/sunday_data.py recorded for it."""
    rel = next(
        (
            p
            for p in (pkg / "release-sunday", pkg.parent / "release-sunday")
            if (p / "facts.json").is_file()
        ),
        None,
    )
    if not rel:
        return None, None, None
    digest = tree_digest(rel)
    rec = None
    man = rel.parent / "release-sunday.manifest.json"
    if man.is_file():
        try:
            rec = json.loads(man.read_text(encoding="utf-8")).get("sha256")
        except ValueError:
            rec = "unreadable"
    return rel, digest, rec


# Fallback chain, decided by Niklas when the submission repo is final: "A" = rc -> safe, "B" = rc -> safe -> F1R -> F1.
CHAIN = "B"
SAFE_ENV = {"PARLEY_PF_MOVER": "0", "PARLEY_CASH_RATE": "0.15", "PARLEY_NOTES_RATE": "1", "PARLEY_CASH_STRICT": "0",
            "PARLEY_ACK_OPENING": "0", "PARLEY_V1_RFQ": "0", "PARLEY_RFQ_TAKE_GAP": "0.015",
            "PARLEY_RULES": "0"}  # fmt: skip


class Build:
    def __init__(self, name, spec, root, env=None, same_as=None):
        self.name, self.spec, self.dir = name, spec, root / name
        self.sha, self.source = None, None
        self.env, self.same_as = env or {}, same_as

    @property
    def agent(self):
        return self.dir / "agent"

    def label(self):
        extra = " flags off" if self.env else ""
        return f"{self.name} ({self.spec} @ {(self.sha or '?')[:7]}{extra})"


def resolve(build):
    src = Path(build.spec)
    if src.is_dir() and (src / ".git").exists():
        build.source = src.resolve()
        build.sha = git("rev-parse", "HEAD", cwd=src)
        if git("status", "--porcelain", "--untracked-files=no", cwd=src):
            print(
                f"[ops] warning: {src} has uncommitted changes; the checkout uses its HEAD {build.sha[:7]}"
            )
    else:
        build.source = None
        build.sha = git("rev-parse", "--verify", f"{build.spec}^{{commit}}")
    return build


def remove_checkout(path):
    if not path.exists():
        return
    if (path / ".git").is_file():
        git("worktree", "remove", "--force", str(path), check=False)
    if path.exists():
        shutil.rmtree(path, onerror=lambda f, p, _: (os.chmod(p, 0o700), f(p)))
    git("worktree", "prune", check=False)


def prepare_or_share(build, refresh=False, log=None):
    if build.same_as is None:
        return prepare(build, refresh, log)
    build.dir, build.sha, build.source = build.same_as.dir, build.same_as.sha, build.same_as.source
    return build


def prepare(build, refresh=False, log=None):
    """A clean checkout of the build's commit with a venv synced to its uv.lock. Reused when it is
    already at that commit, clean and synced."""
    resolve(build)
    marker = build.dir / ".parley-ready"
    lock = build.agent / "uv.lock"
    if build.dir.exists() and not refresh:
        here = git("rev-parse", "HEAD", cwd=build.dir, check=False)
        dirty = git(
            "status", "--porcelain", "--untracked-files=no", cwd=build.dir, check=False
        )
        if here == build.sha and not dirty and marker.is_file() and lock.is_file():
            if (
                marker.read_text(encoding="utf-8").strip()
                == f"{build.sha} {hashlib.sha256(lock.read_bytes()).hexdigest()}"
            ):
                say(f"checkout {build.label()}: ready at {build.dir}", log)
                return build
        say(
            f"checkout {build.name}: at {here[:7] or '?'}{' (dirty)' if dirty else ''}, want {build.sha[:7]}: rebuilding",
            log,
        )
    remove_checkout(build.dir)
    build.dir.parent.mkdir(parents=True, exist_ok=True)
    if build.source:
        git(
            "clone",
            "--quiet",
            "--no-hardlinks",
            str(build.source),
            str(build.dir),
            cwd=build.dir.parent,
        )
        git("checkout", "--quiet", "--detach", build.sha, cwd=build.dir)
    else:
        git("worktree", "prune", check=False)
        git("worktree", "add", "--quiet", "--detach", str(build.dir), build.sha)
    if not lock.is_file():
        sys.exit(f"checkout {build.label()}: no agent/uv.lock")
    uv = shutil.which("uv") or sys.exit("uv not found on PATH")
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    p = subprocess.run([uv, "sync", "--locked", "--quiet"], cwd=build.agent, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")  # fmt: skip
    if p.returncode:
        sys.exit(
            f"checkout {build.label()}: `uv sync --locked` failed:\n{p.stdout}{p.stderr}"
        )
    marker.write_text(
        f"{build.sha} {hashlib.sha256(lock.read_bytes()).hexdigest()}\n",
        encoding="utf-8",
    )
    say(f"checkout {build.label()}: built at {build.dir}", log)
    return build


def api(env):
    key = env.get("ARENA_API_KEY")
    return httpx.Client(base_url=env["ARENA_URL"].rstrip("/") + "/v1", headers={"X-API-Key": key} if key else {},
                        timeout=30)  # fmt: skip


def open_sessions(http, set_):
    try:
        r = http.get("/sessions", params={"set": set_})
    except httpx.HTTPError as e:
        return None, str(e)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    rows = r.json()
    rows = rows.get("sessions", rows) if isinstance(rows, dict) else rows
    return [s for s in rows if s.get("status") == "open"], None


class Status(threading.Thread):
    """Polls the final case list (the agent's own endpoint, never the board or the feed)."""

    def __init__(self, env, waves, every, log):
        super().__init__(daemon=True)
        self.http, self.waves, self.every, self.log = api(env), waves, every, log
        self.closed, self.stop, self.last = set(), threading.Event(), ""
        self.all_closed_at = None

    def poll(self):
        try:
            r = self.http.get("/cases", params={"set": "final"})
        except httpx.HTTPError as e:
            return f"case list unreachable: {e.__class__.__name__}"
        if r.status_code == 403:
            return "waiting: no wave started yet (403)"
        if r.status_code == 401:
            return "ERROR 401: the arena key is missing or unknown"
        if r.status_code != 200:
            return f"case list: HTTP {r.status_code}"
        waves = {}
        for c in r.json():
            w = waves.setdefault(
                c["wave"],
                {"theme": c.get("theme"), "open": False, "left": None, "n": 0},
            )
            w["n"] += 1
            if c.get("open"):
                w["open"], w["left"] = True, c.get("seconds_left")
        for n, w in waves.items():
            if not w["open"]:
                self.closed.add(n)
        if len(self.closed) >= self.waves and self.all_closed_at is None:
            self.all_closed_at = time.time()
        running = [
            f"wave {n} '{w['theme']}' open, {w['left']} s left, {w['n']} cases"
            for n, w in waves.items()
            if w["open"]
        ]
        closed = ",".join(map(str, sorted(self.closed))) or "-"
        return f"{' | '.join(running) or 'between waves'} | closed {closed} ({len(self.closed)}/{self.waves})"

    def run(self):
        while not self.stop.is_set():
            msg = self.poll()
            if msg != self.last or "left" in msg:
                say(f"status: {msg}", self.log)
                self.last = msg
            self.stop.wait(self.every)


def pump(proc, log, flags):
    for line in iter(proc.stdout.readline, ""):
        line = line.rstrip("\n")
        print(line, flush=True)
        log.write(f"{now()} {line}\n")
        log.flush()
        if STALE_DATA in line:
            flags["stale_data"] = True


def summary(env, log):
    rows, err = None, None
    try:
        r = api(env).get("/sessions", params={"set": "final"})
        rows = r.json() if r.status_code == 200 else None
        err = None if rows is not None else f"HTTP {r.status_code}"
    except httpx.HTTPError as e:
        err = str(e)
    if rows is None:
        say(f"summary: sessions not available ({err})", log)
        return
    rows = rows.get("sessions", rows) if isinstance(rows, dict) else rows
    say(f"summary: {len(rows)} final sessions", log)
    scores = []
    for s in sorted(rows, key=lambda s: s.get("case_id", "")):
        if s.get("score") is not None:
            scores.append(s["score"])
        say(f"  {s.get('case_id')}  {s.get('status')}  {s.get('end_reason')}  deal={s.get('deal')}  "
            f"score={s.get('score')}  rounds={s.get('rounds')}  sid={s.get('session_id')}", log)  # fmt: skip
    if scores:
        say(
            f"  mean score {sum(scores) / len(scores):.3f} over {len(scores)} sessions",
            log,
        )


def write_state(state):
    state["updated"] = datetime.now().isoformat(timespec="seconds")
    tmp = STATE.with_suffix(".tmp")
    for attempt in range(20):
        try:
            tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
            os.replace(tmp, STATE)
            return
        except OSError as e:  # Windows: a reader (status view) holds the file open
            if attempt == 19:
                print(f"[ops] state file not written: {e}", flush=True)
                return
            time.sleep(0.05)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--waves", type=int, default=5)
    ap.add_argument("--parallel", type=int, default=10)
    ap.add_argument(
        "--rc",
        default="HEAD",
        help="candidate: a git ref of this repo, or a path to a repo (submission clone)",
    )
    ap.add_argument(
        "--no-safe",
        action="store_true",
        help="skip the first fallback: the candidate itself with the new flags off (SAFE_ENV)",
    )
    ap.add_argument(
        "--chain",
        choices=("A", "B"),
        default=os.environ.get("PARLEY_FINAL_CHAIN", CHAIN),
        help="A: rc -> safe only (same commit); B: rc -> safe -> F1R -> F1",
    )
    ap.add_argument(
        "--fallback", default="F1R", help="first fallback ref ('none' to disable)"
    )
    ap.add_argument(
        "--fallback2", default="F1", help="second fallback ref ('none' to disable)"
    )
    ap.add_argument(
        "--root",
        default=os.environ.get("PARLEY_FINAL_ROOT", str(DEFAULT_ROOT)),
        help="checkout folder",
    )
    ap.add_argument(
        "--in-place",
        action="store_true",
        help="run this repo's agent/ (no checkouts, no fallback)",
    )
    ap.add_argument(
        "--prepare-only",
        action="store_true",
        help="build the checkouts and their venvs, then exit",
    )
    ap.add_argument(
        "--refresh",
        action="store_true",
        help="rebuild the checkouts even when they look ready",
    )
    llm = ap.add_mutually_exclusive_group()
    llm.add_argument(
        "--no-llm", action="store_true", help="templates and rules only (the default)"
    )
    llm.add_argument(
        "--with-llm", action="store_true", help="let parley use the models"
    )
    llm.add_argument(
        "--mode",
        choices=["hybrid", "full"],
        help="parley --mode (candidate builds that support it)",
    )
    ap.add_argument(
        "--data",
        help="data package folder (default: agent/data/package or the kit's data/)",
    )
    ap.add_argument(
        "--journal-dir",
        default=str(AGENT / "runs" / "journal"),
        help="shared PARLEY_JOURNAL_DIR",
    )
    ap.add_argument("--restart-wait", type=float, default=3)
    ap.add_argument(
        "--crash-limit",
        type=int,
        default=3,
        help="exits within --crash-window that switch the build",
    )
    ap.add_argument("--crash-window", type=float, default=120)
    ap.add_argument("--status-every", type=float, default=20)
    ap.add_argument("--max-restarts", type=int, default=500)
    ap.add_argument("--label", default="final", help="log file prefix")
    ap.add_argument(
        "--allow-open-sessions",
        action="store_true",
        help="start although practice/sandbox sessions are open",
    )
    ap.add_argument(
        "--skip-loop-check",
        action="store_true",
        help="local rehearsal only: ignore other agent processes",
    )
    a = ap.parse_args()

    root = Path(a.root)
    builds = []
    if not a.in_place:
        specs = [("rc", a.rc)] + ([] if a.chain == "A" else [("fallback", a.fallback), ("fallback2", a.fallback2)])
        builds = [
            Build(name, spec, root)
            for name, spec in specs
            if spec and spec.lower() != "none"
        ]
        if not a.no_safe and builds and builds[0].name == "rc":
            builds.insert(1, Build("safe", a.rc, root, env=SAFE_ENV, same_as=builds[0]))
    if a.prepare_only:
        for b in builds:
            prepare_or_share(b, a.refresh)
        print("\n".join(f"  {b.label()}: {b.dir}" for b in builds))
        return

    env = child_env()
    if not env.get("ARENA_API_KEY"):
        sys.exit("ARENA_API_KEY is not set (repo-root .env or environment)")
    loops = [] if a.skip_loop_check else agent_processes()
    if loops:
        lines = "\n".join(f"  pid {pid}: {cmd}" for pid, cmd in loops)
        sys.exit(
            f"refusing to start: practice loops are running - stop them first:\n{lines}"
        )
    http = api(env)
    for set_ in ("practice", "sandbox"):
        rows, err = open_sessions(http, set_)
        if err:
            print(f"[ops] warning: could not list {set_} sessions ({err})", flush=True)
        elif rows and not a.allow_open_sessions:
            ids = ", ".join(f"{s.get('case_id')}" for s in rows)
            sys.exit(f"refusing to start: {len(rows)} open {set_} session(s) ({ids}) count against the 10 open "
                     "sessions; wait until they time out or pass --allow-open-sessions")  # fmt: skip
    take_lock()
    log_path = today_dir() / f"{a.label}-{stamp()}.log"
    log = log_path.open("a", encoding="utf-8")
    say(f"log: {log_path}", log)
    try:
        for b in builds:
            prepare_or_share(b, a.refresh, log)
    except SystemExit:
        release_lock()
        raise
    if a.in_place:
        here = Build("in-place", "working tree", ROOT)
        here.dir, here.sha = ROOT, git("rev-parse", "HEAD")
        builds = [here]
    pkg = data_dir(a.data)
    rel, digest, recorded = release_manifest(pkg)
    journal = Path(a.journal_dir).resolve()
    journal.mkdir(parents=True, exist_ok=True)
    env.update(ARENA_DATA=str(pkg), PARLEY_JOURNAL_DIR=str(journal))
    mode = a.mode or ("full" if a.with_llm else "nollm")
    say(
        f"arena: {env['ARENA_URL']}  waves: {a.waves}  parallel: {a.parallel}  mode: {mode}",
        log,
    )
    say(f"data: {pkg}", log)
    if rel:
        ok = recorded == digest
        say(
            f"release: {rel} sha256 {digest[:16]}  recorded {str(recorded)[:16]}  {'OK' if ok else 'MISMATCH or not recorded'}",
            log,
        )
    else:
        say("release: none next to the package", log)
    say(f"journals: {journal}", log)
    for i, b in enumerate(builds):
        say(f"build {i}: {b.label()} at {b.dir}", log)
    uv = shutil.which("uv") or sys.exit("uv not found on PATH")

    def command(b):
        cmd = [uv, "run", "--frozen", "--project", str(b.agent), "parley", "run", "--set", "final",
               "--waves", str(a.waves), "--parallel", str(a.parallel), "--data", str(pkg)]  # fmt: skip
        if a.mode:
            cmd += ["--mode", a.mode]
        elif not a.with_llm:
            cmd.append("--no-llm")
        return cmd

    state = {"pid": os.getpid(), "log": str(log_path), "builds": [{"name": b.name, "spec": b.spec, "sha": b.sha, "env": b.env} for b in builds],
             "active": builds[0].name, "sha": builds[0].sha, "attempt": 0, "restarts": 0, "switches": [], "exits": [],
             "mode": mode, "running": False}  # fmt: skip
    write_state(state)
    status = Status(env, a.waves, a.status_every, log)
    status.start()
    proc, idx, exits, flags = None, 0, deque(), {}
    exit_code = 0
    try:
        while True:
            b = builds[idx]
            t0 = time.time()
            state.update(
                active=b.name,
                sha=b.sha,
                attempt=state["attempt"] + 1,
                running=True,
                started_at=now(),
            )
            write_state(state)
            say(f"starting runner {b.label()} (attempt {state['attempt']})", log)
            proc = subprocess.Popen(command(b), cwd=b.agent, env={**env, **b.env}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace", bufsize=1)  # fmt: skip
            state["runner_pid"] = proc.pid
            write_state(state)
            reader = threading.Thread(target=pump, args=(proc, log, flags), daemon=True)
            reader.start()
            while proc.poll() is None:
                done_at = status.all_closed_at
                if done_at and time.time() - done_at > GRACE_AFTER_LAST_WAVE_S:
                    say("all waves closed and the runner is still up: stopping it", log)
                    kill_tree(proc)
                    break
                time.sleep(1)
            reader.join(timeout=5)
            rc, ran = proc.returncode, time.time() - t0
            state["running"] = False
            if rc == 0 or len(status.closed) >= a.waves:
                say(
                    f"runner finished (rc {rc}) after {ran:.0f} s; waves closed: {sorted(status.closed)}",
                    log,
                )
                state["finished"] = now()
                write_state(state)
                break
            if rc == EXIT_NO_DATA:
                for _ in range(3):
                    shout(f"THE RUNNER HAS NO DATA PACKAGE ({pkg}): NOT RESTARTING. Run ops/sunday-data (or "
                          "`uv run parley data`), then start ops/final again", log)  # fmt: skip
                state.update(stopped="no data package", exits=(state["exits"] + [{"at": now(), "build": b.name, "rc": rc,
                             "ran_s": round(ran)}])[-20:])  # fmt: skip
                write_state(state)
                exit_code = EXIT_NO_DATA
                break
            state["restarts"] += 1
            state["exits"] = (
                state["exits"]
                + [{"at": now(), "build": b.name, "rc": rc, "ran_s": round(ran)}]
            )[-20:]
            write_state(state)
            if flags.pop("stale_data", False):
                shout(
                    "the runner refuses stale data: run ops/sunday-data now (the supervisor keeps retrying)",
                    log,
                )
            if state["restarts"] > a.max_restarts:
                shout(f"giving up after {state['restarts']} restarts", log)
                break
            t = time.time()
            exits.append(t)
            while exits and t - exits[0] > a.crash_window:
                exits.popleft()
            wait = a.restart_wait
            if len(exits) >= a.crash_limit:
                if idx + 1 < len(builds):
                    old, idx = b, idx + 1
                    exits.clear()
                    shout(f"{a.crash_limit} exits within {a.crash_window:.0f} s on {old.label()}: "
                          f"SWITCHING TO FALLBACK {builds[idx].label()}", log)  # fmt: skip
                    state["switches"].append(
                        {
                            "at": now(),
                            "from": old.name,
                            "to": builds[idx].name,
                            "sha": builds[idx].sha,
                        }
                    )
                    write_state(state)
                else:
                    wait = min(
                        30, a.restart_wait * 2 ** min(len(exits) - a.crash_limit + 1, 4)
                    )
                    shout(
                        f"the last build {b.label()} keeps exiting: retrying every {wait:.0f} s - check the log",
                        log,
                    )
            say(
                f"runner exited with rc {rc} after {ran:.0f} s: restarting in {wait:.0f} s",
                log,
            )
            time.sleep(wait)
    except KeyboardInterrupt:
        say("interrupted: stopping the runner", log)
        if proc and proc.poll() is None:
            kill_tree(proc)
    finally:
        status.stop.set()
        state["running"] = False
        write_state(state)
        summary(env, log)
        release_lock()
        log.close()
    if exit_code:
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
