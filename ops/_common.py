"""Shared helpers for the ops scripts: paths, .env handling (values never printed), subprocess runs,
process discovery and a PASS/FAIL table."""

import hashlib
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "ops"
AGENT = ROOT / "agent"
KIT = ROOT / "internal" / "briefing" / "tacto" / "repo"
STARTER = KIT / "starter-kit"
LOGS = OPS / "logs"
ENV_FILE = ROOT / ".env"
SECRET_KEYS = ("ARENA_API_KEY", "ANTHROPIC_API_KEY")
PASS_KEYS = (
    "ARENA_URL",
    "ARENA_API_KEY",
    "ARENA_DATA",
    "ANTHROPIC_API_KEY",
    "PLAN_MODEL",
    "WRITE_MODEL",
    "ANALYST_MODEL",
)
DEFAULT_URL = "https://arena.tacto.ai"


def read_env_file(path=ENV_FILE):
    out = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if sep:
            out[key.strip()] = value.strip().strip("\"'")
    return out


def child_env(overrides=None):
    """Environment for the agent: the real environment wins, then the repo-root .env, then overrides."""
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    for key, value in read_env_file().items():
        if key in PASS_KEYS and value and key not in env:
            env[key] = value
    env.setdefault("ARENA_URL", DEFAULT_URL)
    env.update(overrides or {})
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def mask(value):
    return "<empty>" if not value else f"<set, {len(value)} chars>"


def today_dir():
    d = LOGS / datetime.now().strftime("%Y-%m-%d")
    d.mkdir(parents=True, exist_ok=True)
    return d


def stamp():
    return datetime.now().strftime("%H%M%S")


def run(cmd, cwd=None, env=None, timeout=600, check=False):
    """Run a command, capture text output. Never raises on a missing binary: returns rc 127."""
    try:
        p = subprocess.run(
            cmd,
            cwd=cwd,
            env=env or child_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError as e:
        return subprocess.CompletedProcess(cmd, 127, "", str(e))
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(
            cmd, 124, e.stdout or "", f"timeout after {timeout}s"
        )
    if check and p.returncode:
        sys.exit(
            f"{' '.join(map(str, cmd))} failed ({p.returncode}):\n{p.stdout}\n{p.stderr}"
        )
    return p


AGENT_PATTERNS = (
    ("parley", "play"),
    ("parley", "run"),
    ("arena", "play"),
    ("arena", "run"),
)


def agent_processes(exclude_pids=()):
    """Running negotiation loops (parley/arena play|run), other than the given PIDs and our own tree."""
    import psutil

    me = psutil.Process()
    skip = {me.pid, *exclude_pids}
    try:
        skip |= {p.pid for p in me.parents()}
        skip |= {p.pid for p in me.children(recursive=True)}
    except psutil.Error:
        pass
    found = []
    for p in psutil.process_iter(["pid", "cmdline"]):
        if p.info["pid"] in skip:
            continue
        cmd = p.info.get("cmdline") or []
        if not cmd:
            continue
        joined = " ".join(cmd).replace("\\", "/")
        tokens = [os.path.basename(t).removesuffix(".exe").lower() for t in cmd]
        for prog, verb in AGENT_PATTERNS:
            if prog in tokens and verb in tokens[tokens.index(prog) + 1 :]:
                found.append((p.info["pid"], joined[:160]))
                break
    return found


def kill_tree(proc):
    import psutil

    try:
        parent = psutil.Process(proc.pid)
        kids = parent.children(recursive=True)
    except psutil.Error:
        return
    for p in [*kids, parent]:
        try:
            p.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs([*kids, parent], timeout=8)
    for p in alive:
        try:
            p.kill()
        except psutil.Error:
            pass


class Table:
    def __init__(self):
        self.rows = []

    def add(self, check, status, detail=""):
        self.rows.append((check, status, detail))
        print(f"  [{status:<4}] {check}: {detail}", flush=True)

    def render(self):
        w = max(len(r[0]) for r in self.rows) if self.rows else 10
        lines = [f"{'CHECK'.ljust(w)}  STATUS  DETAIL", f"{'-' * w}  ------  ------"]
        lines += [f"{c.ljust(w)}  {s:<6}  {d}" for c, s, d in self.rows]
        return "\n".join(lines)

    @property
    def failed(self):
        return [r for r in self.rows if r[1] == "FAIL"]


def tree_digest(folder):
    """sha256 over the relative paths and contents of every file under `folder`."""
    h = hashlib.sha256()
    for p in sorted(x for x in Path(folder).rglob("*") if x.is_file()):
        h.update(p.relative_to(folder).as_posix().encode() + b"\0")
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()
