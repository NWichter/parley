# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27", "psutil>=5.9"]
# ///
"""Sunday-morning pre-flight: keys, kit, install, tests, arena key, model access, clock, disk,
power, network. Prints a PASS/WARN/FAIL table. Plays nothing unless --play-one (sandbox D01, or --case case:P01 for Tacto's practice pre-flight)."""

import argparse
import json
import os
import platform
import re
import shutil
import sys
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
import psutil
from _common import (
    AGENT,
    KIT,
    STARTER,
    Table,
    agent_processes,
    child_env,
    read_env_file,
    run,
    stamp,
    today_dir,
)

REQUIRED = ("ARENA_API_KEY", "ANTHROPIC_API_KEY")
OPTIONAL = ("PLAN_MODEL", "WRITE_MODEL", "ANALYST_MODEL", "ARENA_URL")
DEFAULT_MODELS = {
    "PLAN_MODEL": "claude-opus-5-5",
    "WRITE_MODEL": "claude-sonnet-5-5",
    "ANALYST_MODEL": "claude-haiku-5-5",
}
ANTHROPIC = "https://api.anthropic.com/v1"


def check_env(t):
    file_env = read_env_file()
    env = child_env()
    for k in REQUIRED:
        src = "environment" if k in os.environ else ".env"
        t.add(
            f"env {k}",
            "PASS" if env.get(k) else "FAIL",
            f"present ({src})" if env.get(k) else "missing",
        )
    for k in OPTIONAL:
        if env.get(k):
            t.add(f"env {k}", "PASS", env[k])
        else:
            t.add(
                f"env {k}",
                "WARN",
                f"not set (default {DEFAULT_MODELS.get(k, 'https://arena.tacto.ai')})",
            )
    if not file_env:
        t.add(
            "repo-root .env",
            "WARN",
            "no .env file; keys come from the environment only",
        )
    return env


def check_kit(t):
    if not (KIT / ".git").exists():
        t.add("kit clone", "FAIL", f"no git clone at {KIT}")
        return
    before = run(["git", "-C", str(KIT), "rev-parse", "--short", "HEAD"]).stdout.strip()
    p = run(["git", "-C", str(KIT), "pull", "--ff-only"], timeout=120)
    after = run(["git", "-C", str(KIT), "rev-parse", "--short", "HEAD"]).stdout.strip()
    if p.returncode:
        t.add(
            "kit git pull",
            "WARN",
            f"pull failed, staying on {before}: {p.stderr.strip()[:120]}",
        )
    elif before == after:
        t.add("kit git pull", "PASS", f"up to date ({after})")
    else:
        log = (
            run(["git", "-C", str(KIT), "log", "--oneline", f"{before}..{after}"])
            .stdout.strip()
            .splitlines()
        )
        t.add(
            "kit git pull",
            "WARN",
            f"updated {before} -> {after} ({len(log)} commits): re-run tests, check agent/parley/kit",
        )


def check_install(t):
    for name, cwd, extra in (
        ("uv sync agent", AGENT, []),
        ("uv sync kit", STARTER, ["--all-extras"]),
    ):
        p = run(["uv", "sync", *extra], cwd=cwd, timeout=600)
        t.add(
            name,
            "PASS" if p.returncode == 0 else "FAIL",
            "ok" if p.returncode == 0 else p.stderr.strip()[-160:],
        )


def check_tests(t):
    p = run(["uv", "run", "pytest", "-q"], cwd=AGENT, timeout=900)
    tail = (p.stdout.strip().splitlines() or [""])[-1]
    t.add("agent unit tests", "PASS" if p.returncode == 0 else "FAIL", tail[:160])


def check_arena(t, env):
    url = env["ARENA_URL"].rstrip("/")
    headers = {"X-API-Key": env["ARENA_API_KEY"]} if env.get("ARENA_API_KEY") else {}
    try:
        t0 = time.monotonic()
        h = httpx.get(f"{url}/health", timeout=15)
        ms = (time.monotonic() - t0) * 1000
        t.add(
            "network arena /health",
            "PASS" if h.status_code == 200 else "FAIL",
            f"HTTP {h.status_code}, {ms:.0f} ms",
        )
        if (date := h.headers.get("Date")) is not None:
            skew = (datetime.now(UTC) - parsedate_to_datetime(date)).total_seconds()
            status = "PASS" if abs(skew) < 3 else ("WARN" if abs(skew) < 30 else "FAIL")
            t.add(
                "clock skew vs arena",
                status,
                f"{skew:+.1f} s (local - server, 1 s resolution)",
            )
        else:
            t.add("clock skew vs arena", "WARN", "no Date header")
    except httpx.HTTPError as e:
        t.add("network arena /health", "FAIL", e.__class__.__name__)
        return
    try:
        r = httpx.get(f"{url}/v1/me", headers=headers, timeout=30)
    except httpx.HTTPError as e:
        t.add("arena GET /v1/me", "FAIL", e.__class__.__name__)
        return
    if r.status_code != 200:
        t.add("arena GET /v1/me", "FAIL", f"HTTP {r.status_code}")
        return
    me = r.json()
    pr = me.get("practice") or {}
    status = "PASS" if me.get("status") == "active" else "FAIL"
    t.add(
        "arena GET /v1/me",
        status,
        f"team '{me.get('team_name')}', {me.get('status')}, practice {pr.get('used')}/{pr.get('limit')} used",
    )
    for set_ in ("practice", "sandbox"):
        try:
            s = httpx.get(
                f"{url}/v1/sessions", params={"set": set_}, headers=headers, timeout=30
            )
            rows = s.json() if s.status_code == 200 else []
            rows = rows.get("sessions", rows) if isinstance(rows, dict) else rows
            open_ = [x for x in rows if x.get("status") == "open"]
            t.add(
                f"open {set_} sessions",
                "PASS" if not open_ else "WARN",
                f"{len(open_)} open (block the 10-session limit)",
            )
        except (httpx.HTTPError, ValueError) as e:
            t.add(f"open {set_} sessions", "WARN", e.__class__.__name__)
    try:
        f = httpx.get(
            f"{url}/v1/cases", params={"set": "final"}, headers=headers, timeout=30
        )
        t.add("final case list", "PASS" if f.status_code in (200, 403) else "WARN",
              "403 = waiting for wave 1 (expected before 13:30)" if f.status_code == 403 else f"HTTP {f.status_code}")  # fmt: skip
    except httpx.HTTPError as e:
        t.add("final case list", "WARN", e.__class__.__name__)


def check_anthropic(t, env):
    key = env.get("ANTHROPIC_API_KEY")
    if not key:
        t.add("anthropic API", "FAIL", "no key")
        return
    h = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    for var in ("PLAN_MODEL", "WRITE_MODEL", "ANALYST_MODEL"):
        model = env.get(var) or DEFAULT_MODELS[var]
        try:
            r = httpx.get(f"{ANTHROPIC}/models/{model}", headers=h, timeout=20)
            t.add(
                f"model {var}",
                "PASS" if r.status_code == 200 else "FAIL",
                f"{model}: HTTP {r.status_code}",
            )
        except httpx.HTTPError as e:
            t.add(f"model {var}", "FAIL", f"{model}: {e.__class__.__name__}")
    model = env.get("ANALYST_MODEL") or DEFAULT_MODELS["ANALYST_MODEL"]
    body = {
        "model": model,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }
    try:
        t0 = time.monotonic()
        r = httpx.post(f"{ANTHROPIC}/messages", headers=h, json=body, timeout=60)
        ms = (time.monotonic() - t0) * 1000
        limits = {
            k: v
            for k, v in r.headers.items()
            if k.startswith("anthropic-ratelimit-") and k.endswith("remaining")
        }
        lim = ", ".join(
            f"{k.removeprefix('anthropic-ratelimit-')}={v}"
            for k, v in sorted(limits.items())
        )
        detail = f"{model} 1-token call HTTP {r.status_code}, {ms:.0f} ms" + (
            f"; {lim}" if lim else ""
        )
        if r.status_code != 200:
            try:
                detail += f"; {r.json().get('error', {}).get('message', '')[:100]}"
            except ValueError:
                pass
        t.add(
            "anthropic 1-token call", "PASS" if r.status_code == 200 else "FAIL", detail
        )
    except httpx.HTTPError as e:
        t.add("anthropic 1-token call", "FAIL", e.__class__.__name__)


def check_machine(t):
    free = shutil.disk_usage(AGENT).free / 1e9
    t.add(
        "disk space",
        "PASS" if free > 5 else ("WARN" if free > 1 else "FAIL"),
        f"{free:.1f} GB free",
    )
    bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    if bat is None:
        t.add("power supply", "PASS", "no battery (desktop/VM)")
    else:
        t.add(
            "power supply",
            "PASS" if bat.power_plugged else "FAIL",
            f"{'on AC' if bat.power_plugged else 'ON BATTERY'}, {bat.percent:.0f} %",
        )
    system = platform.system()
    if system == "Windows":
        p = run(["powercfg", "/query", "SCHEME_CURRENT", "SUB_SLEEP", "STANDBYIDLE"])
        # the last two hex values are the current AC and DC settings (any Windows display language)
        vals = re.findall(r":\s*0x([0-9a-f]+)\s*$", p.stdout, re.I | re.M)
        if len(vals) >= 2:
            ac_s, dc_s = int(vals[-2], 16), int(vals[-1], 16)
            ok = ac_s == 0
            t.add("sleep on AC", "PASS" if ok else "FAIL",
                  "never" if ok else f"after {ac_s // 60} min: run `powercfg /change standby-timeout-ac 0`")  # fmt: skip
            if dc_s:
                t.add(
                    "sleep on battery",
                    "WARN",
                    f"after {dc_s // 60} min (`powercfg /change standby-timeout-dc 0`)",
                )
        else:
            t.add(
                "sleep on AC",
                "WARN",
                "could not read powercfg: set sleep to never by hand",
            )
        t.add(
            "lid / updates",
            "WARN",
            "reminder: lid close action = do nothing; pause Windows Update for today",
        )
    elif system == "Darwin":
        p = run(["pmset", "-g"])
        m = re.search(r"^\s*sleep\s+(\d+)", p.stdout, re.M)
        ok = bool(m and m.group(1) == "0")
        t.add(
            "sleep",
            "PASS" if ok else "WARN",
            "never" if ok else "run `caffeinate -dimsu` next to the runner",
        )
    else:
        t.add(
            "sleep",
            "WARN",
            "reminder: disable suspend (systemd-inhibit / caffeinate) for the final",
        )


def check_network(t):
    for name, url in (
        ("DNS+TLS api.anthropic.com", "https://api.anthropic.com"),
        ("DNS+TLS github.com", "https://github.com"),
    ):
        try:
            t0 = time.monotonic()
            httpx.head(url, timeout=10)
            t.add(name, "PASS", f"{(time.monotonic() - t0) * 1000:.0f} ms")
        except httpx.HTTPError as e:
            t.add(name, "FAIL", e.__class__.__name__)


def check_data(t):
    candidates = [AGENT / "data" / "package", KIT / "data"]
    pkg = next((p for p in candidates if (p / "facts.json").is_file()), None)
    t.add(
        "data package",
        "PASS" if pkg else "FAIL",
        str(pkg) if pkg else "none: run ops/sunday-data",
    )
    rel = [
        p
        for p in (
            AGENT / "data" / "release-sunday",
            (pkg or Path(".")) / "release-sunday",
        )
        if (p / "facts.json").is_file()
    ]
    after_8 = datetime.now().hour >= 8 and datetime.now().weekday() == 6
    if rel:
        t.add("Sunday release", "PASS", str(rel[0]))
        check_release_reaches_agent(t, pkg, rel[0])
    else:
        t.add(
            "Sunday release",
            "FAIL" if after_8 else "WARN",
            "missing: run ops/sunday-data after 08:00",
        )
    personas = AGENT / "cache" / "personas.json"
    t.add(
        "persona cache",
        "PASS" if personas.is_file() else "WARN",
        str(personas) if personas.is_file() else "missing: parley prepare",
    )


def check_release_reaches_agent(t, pkg, rel):
    """The agent's own data loader merges the release (new fact IDs readable) and the persona cache
    was built on the release."""
    code = (
        "import json,sys; from parley.dossier import load_package; p = load_package(); ids = list(json.l"
        "oad(open(sys.argv[1], encoding='utf-8'))); ok = [i for i in ids if i in p.facts]; print(json.dumps({'release': str(getattr(p, 'release', None)), 'ids': len(ids), 'known': len(ok)}))"
    )
    r = run(["uv", "run", "--frozen", "python", "-c", code, str(rel / "facts.json")], cwd=AGENT, timeout=120)
    try:
        info = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        t.add("release in the agent", "FAIL", (r.stderr or r.stdout).strip()[-200:])
        return
    good = info["release"] not in ("None", "") and info["ids"] and info["known"] == info["ids"]
    t.add("release in the agent", "PASS" if good else "FAIL",
          f"{info['known']}/{info['ids']} release IDs readable, release {info['release']}")  # fmt: skip
    try:
        meta = json.loads((AGENT / "cache" / "personas.json").read_text(encoding="utf-8")).get("_meta", {})
    except (OSError, ValueError):
        meta = {}
    t.add("persona cache on the release", "PASS" if "release" in str(meta.get("data")) else "FAIL",
          str(meta.get("data")))  # fmt: skip


def check_loops(t):
    loops = agent_processes()
    t.add("practice loops", "PASS" if not loops else "WARN",
          "none running" if not loops else "; ".join(f"pid {p}" for p, _ in loops) + ": stop before the final")  # fmt: skip


def play_one(t, env, with_llm, case):
    cmd = ["uv", "run", "parley", "play", case] + ([] if with_llm else ["--no-llm"])
    if case.startswith("case:P"):
        cmd.append("--allow-practice")
    p = run(cmd, cwd=AGENT, env=env, timeout=900)
    out = today_dir() / f"preflight-{case.split(':')[-1]}-{stamp()}.log"
    out.write_text(p.stdout + "\n" + p.stderr, encoding="utf-8")
    m = re.search(r'"score":\s*([0-9.]+|null)', p.stdout)
    t.add(
        case,
        "PASS" if p.returncode == 0 else "FAIL",
        f"rc {p.returncode}, score {m.group(1) if m else '?'}; log {out}",
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--play-one",
        action="store_true",
        help="play exactly one case (default sandbox case:D01, outside the quota)",
    )
    ap.add_argument(
        "--case",
        default="case:D01",
        help="case for --play-one; Tacto's Sunday pre-flight asks for one practice case, e.g. case:P01 (1 session)",
    )
    ap.add_argument(
        "--with-llm",
        action="store_true",
        help="with --play-one: use the models (costs tokens)",
    )
    ap.add_argument(
        "--skip-sync", action="store_true", help="skip kit pull, uv sync and tests"
    )
    a = ap.parse_args()
    t = Table()
    print(
        f"pre-flight {datetime.now():%Y-%m-%d %H:%M:%S} on {platform.node()} ({platform.system()})",
        flush=True,
    )
    env = check_env(t)
    if not a.skip_sync:
        check_kit(t)
        check_install(t)
        check_tests(t)
    check_data(t)
    check_arena(t, env)
    check_anthropic(t, env)
    check_network(t)
    check_machine(t)
    check_loops(t)
    if a.play_one:
        play_one(t, env, a.with_llm, a.case)
    table = t.render()
    print("\n" + table)
    out = today_dir() / f"preflight-{stamp()}.txt"
    out.write_text(table + "\n", encoding="utf-8")
    print(f"\n{len(t.failed)} FAIL, saved to {out}")
    sys.exit(1 if t.failed else 0)


if __name__ == "__main__":
    main()
