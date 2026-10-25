# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27", "psutil>=5.9"]
# ///
"""Sunday 08:05 data release: pull the kit, download package + release with `parley data`, record the
release's sha256 and file list (agent/data/release-sunday.manifest.json, checked by ops/final.py),
rebuild the supplier profiles (`parley prepare --force`) and the structured profile fields if the
agent has an extractor for them, run the tests and a dossier smoke test on three suppliers (one never
played) and print what changed. A missing release prints SUBMIT FALLBACK. --dry-run only snapshots."""

import argparse
import csv
import hashlib
import json
import shutil
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import httpx
from _common import AGENT, KIT, OPS, ROOT, child_env, run, stamp, today_dir, tree_digest

PERSONAS = AGENT / "cache" / "personas.json"


def package_dir():
    for p in (AGENT / "data" / "package", KIT / "data"):
        if (p / "facts.json").is_file():
            return p
    return None


def release_dir(pkg):
    for p in (
        (pkg / "release-sunday") if pkg else None,
        (pkg.parent / "release-sunday") if pkg else None,
        AGENT / "data" / "release-sunday",
    ):
        if p and (p / "facts.json").is_file():
            return p
    return None


def snapshot(env):
    pkg = package_dir()
    snap = {"package": str(pkg) if pkg else None}
    if pkg:
        facts = json.loads((pkg / "facts.json").read_text(encoding="utf-8"))
        snap["facts_by_kind"] = dict(
            sorted(Counter(k.split(":", 1)[0] for k in facts).items())
        )
        db = next(iter(sorted(pkg.rglob("*.sqlite"))), None)
        if db:
            c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            snap["suppliers"] = dict(
                c.execute(
                    "SELECT detail, count(*) FROM suppliers GROUP BY detail"
                ).fetchall()
            )
            snap["tables"] = {t: c.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
                              for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}  # fmt: skip
            try:
                rows = c.execute(
                    "SELECT series_id, max(month) || ' = ' || (SELECT value FROM index_values v2"
                    " WHERE v2.series_id = index_values.series_id ORDER BY month DESC LIMIT 1), count(*)"
                    " FROM index_values GROUP BY series_id"
                ).fetchall()
                snap["index_last"] = {s: [d, n] for s, d, n in rows}
            except sqlite3.Error as e:
                snap["index_last"] = {"error": str(e)}
    rel = release_dir(pkg)
    snap["release"] = str(rel) if rel else None
    if rel:
        rfacts = json.loads((rel / "facts.json").read_text(encoding="utf-8"))
        snap["release_facts_by_kind"] = dict(
            sorted(Counter(k.split(":", 1)[0] for k in rfacts).items())
        )
        snap["release_files"] = sorted(
            str(p.relative_to(rel)).replace("\\", "/")
            for p in rel.rglob("*")
            if p.is_file()
        )
        snap["release_index_facts"] = sorted(k for k in rfacts if k.startswith("idx:"))
    snap["personas"] = (
        len(json.loads(PERSONAS.read_text(encoding="utf-8")))
        if PERSONAS.is_file()
        else 0
    )
    url, key = env["ARENA_URL"].rstrip("/"), env.get("ARENA_API_KEY")
    try:
        snap["downloads"] = httpx.get(f"{url}/v1/data", timeout=20).json()
    except (httpx.HTTPError, ValueError) as e:
        snap["downloads"] = {"error": e.__class__.__name__}
    for set_ in ("practice", "sandbox"):
        try:
            r = httpx.get(
                f"{url}/v1/cases",
                params={"set": set_},
                headers={"X-API-Key": key} if key else {},
                timeout=30,
            )
            snap[f"cases_{set_}"] = (
                len(r.json()) if r.status_code == 200 else f"HTTP {r.status_code}"
            )
        except (httpx.HTTPError, ValueError) as e:
            snap[f"cases_{set_}"] = e.__class__.__name__
    return snap


def diff(a, b):
    out = []
    for key in (
        "package",
        "release",
        "personas",
        "cases_practice",
        "cases_sandbox",
        "suppliers",
    ):
        if a.get(key) != b.get(key):
            out.append(f"{key}: {a.get(key)} -> {b.get(key)}")
    for key in ("facts_by_kind", "release_facts_by_kind", "tables"):
        x, y = a.get(key) or {}, b.get(key) or {}
        for k in sorted(set(x) | set(y)):
            if x.get(k) != y.get(k):
                out.append(f"{key}.{k}: {x.get(k)} -> {y.get(k)}")
    xi, yi = a.get("index_last") or {}, b.get("index_last") or {}
    changed = [s for s in sorted(set(xi) | set(yi)) if xi.get(s) != yi.get(s)]
    if changed:
        out.append(
            f"index series changed: {len(changed)}: "
            + ", ".join(f"{s} {xi.get(s)} -> {yi.get(s)}" for s in changed[:20])
        )
    new_files = sorted(
        set(b.get("release_files") or []) - set(a.get("release_files") or [])
    )
    if new_files:
        out.append(
            f"release files new: {len(new_files)}: "
            + ", ".join(new_files[:30])
            + (" …" if len(new_files) > 30 else "")
        )
    if b.get("release_index_facts"):
        out.append(f"release index facts: {', '.join(b['release_index_facts'][:30])}")
    return out or ["no change"]


def step(name, cmd, cwd, env, timeout=900):
    print(f"\n== {name}: {' '.join(cmd)}", flush=True)
    p = run(cmd, cwd=cwd, env=env, timeout=timeout)
    print((p.stdout + p.stderr).strip()[-2000:], flush=True)
    print(f"-> rc {p.returncode}", flush=True)
    return p.returncode == 0


SMOKE = ("sup:70340", "sup:70520", "sup:70550")


def write_manifest(rel, out):
    files = sorted(p for p in rel.rglob("*") if p.is_file())
    man = {
        "release": str(rel),
        "recorded": datetime.now().isoformat(timespec="seconds"),
        "sha256": tree_digest(rel),
        "files": [
            {
                "path": p.relative_to(rel).as_posix(),
                "bytes": p.stat().st_size,
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            }
            for p in files
        ],  # fmt: skip
    }
    text = json.dumps(man, indent=1)
    (rel.parent / "release-sunday.manifest.json").write_text(text, encoding="utf-8")
    (out / f"release-manifest-{stamp()}.json").write_text(text, encoding="utf-8")
    return man


def profile_fields_cmd():
    """The agent's extractor for cache/profile_fields.json, if one exists yet."""
    cands = [
        AGENT / "scripts" / n
        for n in ("profile_fields.py", "extract_profile_fields.py")
    ]
    cands += sorted((AGENT / "scripts").glob("*field*.py")) + sorted(
        (AGENT / "parley").glob("*field*.py")
    )
    for p in cands:
        if p.is_file():
            cmd = ["uv", "run", "--frozen", "python"]
            cmd += ["-m", f"parley.{p.stem}"] if p.parent.name == "parley" else [str(p)]
            helptext = run([*cmd, "--help"], cwd=AGENT, timeout=120).stdout
            return cmd + (["--force"] if "--force" in helptext else [])
    helptext = run(
        ["uv", "run", "--frozen", "parley", "--help"], cwd=AGENT, timeout=120
    ).stdout
    subs = (
        helptext.split("{", 1)[1].split("}", 1)[0].split(",") if "{" in helptext else []
    )
    for sub in ("fields", "profile-fields", "extract-fields"):
        if sub in subs:
            return ["uv", "run", "--frozen", "parley", sub]
    return None


def release_supplier_ids(rel):
    """Suppliers with their own letters or news in the release (the memo has none)."""
    out = set()
    for t in ("documents", "news"):
        f = rel / "data" / f"{t}.csv"
        if f.is_file():
            with f.open(encoding="utf-8") as fh:
                out |= {r["supplier_id"] for r in csv.DictReader(fh) if r.get("supplier_id")}
    return out


def persona_diff(old, new):
    """Changes of the persona fields the policy reads (cash_short, hedged_series, patience)."""
    lines = []
    for sid in sorted(set(old) | set(new)):
        if sid == "_meta":
            continue
        a, b = old.get(sid) or {}, new.get(sid) or {}
        for f in ("cash_short", "hedged_series", "patience"):
            if a.get(f) != b.get(f):
                lines.append(f"{sid} {f}: {a.get(f)} -> {b.get(f)}")
    return lines


def keep_unchanged(old, rel):
    """After `prepare --force`: put back the old profile of every supplier the release does not touch,
    so model noise does not change them on the morning of the final."""
    new = json.loads(PERSONAS.read_text(encoding="utf-8"))
    touched = release_supplier_ids(rel)
    kept = [sid for sid in old if sid != "_meta" and sid not in touched and sid in new]
    for sid in kept:
        new[sid] = old[sid]
    PERSONAS.write_text(json.dumps(new, indent=1, ensure_ascii=False), encoding="utf-8")
    return kept, sorted(touched & new.keys())


def release_public(env):
    try:
        info = (
            httpx.get(f"{env['ARENA_URL'].rstrip('/')}/v1/data", timeout=20)
            .json()
            .get("release-sunday")
            or {}
        )
    except (httpx.HTTPError, ValueError) as e:
        return None, e.__class__.__name__
    return bool(info.get("url")), info


def fallback_sha(ref):
    p = run(["git", "-C", str(ROOT), "rev-parse", "--short", f"{ref}^{{commit}}"])
    return p.stdout.strip() or "?"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--dry-run", action="store_true", help="snapshot and plan only, change nothing"
    )
    ap.add_argument(
        "--no-force",
        action="store_true",
        help="keep cached profiles (prepare without --force)",
    )
    ap.add_argument(
        "--no-llm", action="store_true", help="skip `parley prepare` (no plan model)"
    )
    ap.add_argument(
        "--wait-release",
        type=float,
        default=0,
        help="minutes to wait for the release to go public",
    )
    ap.add_argument(
        "--smoke",
        nargs="*",
        default=list(SMOKE),
        help="suppliers for the dossier smoke test",
    )
    ap.add_argument(
        "--fallback",
        default="F1R",
        help="the build to submit when the release is missing",
    )
    ap.add_argument(
        "--keep-unchanged",
        action="store_true",
        help="after prepare, restore the old profiles of suppliers without release letters or news",
    )
    ap.add_argument(
        "--release-from",
        help="rehearsal only: copy this unpacked release (e.g. agent/data/release-sample/release-sunday) "
        "next to the package after `parley data`, as if it had been downloaded",
    )
    a = ap.parse_args()
    env = child_env()
    out = today_dir()
    deadline = time.time() + a.wait_release * 60
    while True:
        public, info = release_public(env)
        if public or time.time() >= deadline:
            break
        print(f"release not public yet ({info}): checking again in 30 s", flush=True)
        time.sleep(30)
    before = snapshot(env)
    (out / f"data-before-{stamp()}.json").write_text(
        json.dumps(before, indent=1), encoding="utf-8"
    )
    rel_info = (before.get("downloads") or {}).get("release-sunday") or {}
    print(
        f"package: {before.get('package')}\nrelease: {before.get('release')}\nrelease on the arena: {rel_info}"
    )
    prepare = ["uv", "run", "parley", "prepare", *([] if a.no_force else ["--force"])]
    plan = [
        *([("kit pull", ["git", "-C", str(KIT), "pull", "--ff-only"], None)] if (KIT / ".git").exists() else []),
        ("uv sync", ["uv", "sync", "--locked"], AGENT),
        ("parley data", ["uv", "run", "parley", "data"], AGENT),
        ("parley prepare", prepare, AGENT),
        ("profile fields", None, AGENT),
        ("tests", ["uv", "run", "pytest", "-q"], AGENT),
        (
            "dossier smoke",
            [
                "uv",
                "run",
                "--frozen",
                "python",
                str(OPS / "smoke_dossier.py"),
                *a.smoke,
            ],
            AGENT,
        ),
    ]
    if a.no_llm:
        plan = [p for p in plan if p[0] != "parley prepare"]
    if a.dry_run:
        print("\ndry run, would run:")
        for name, cmd, cwd in plan:
            if name == "profile fields":
                cmd = profile_fields_cmd() or [
                    "(no extractor for cache/profile_fields.json yet: skipped)"
                ]
            print(f"  {name}: {' '.join(cmd)}  (in {cwd or '.'})")
        if not rel_info.get("url"):
            print(
                "  note: the Sunday release is not public yet; `parley data` would fetch the package only"
            )
        return
    ok, man, rel_now = {}, None, release_dir(package_dir())
    old_personas = json.loads(PERSONAS.read_text(encoding="utf-8")) if PERSONAS.is_file() else {}
    for name, cmd, cwd in plan:
        if name == "profile fields":
            cmd = profile_fields_cmd()
            if not cmd:
                print(
                    "\n== profile fields: no extractor for agent/cache/profile_fields.json yet: skipped",
                    flush=True,
                )
                continue
        if name == "dossier smoke" and rel_now:
            extra = sorted(release_supplier_ids(rel_now) - set(cmd))[:3]
            cmd = [*cmd, *extra]
        ok[name] = step(
            name,
            cmd,
            cwd,
            env,
            timeout=1800 if name in ("parley prepare", "profile fields") else 900,
        )
        if name == "parley prepare" and ok[name] and PERSONAS.is_file():
            if a.keep_unchanged and rel_now:
                kept, redone = keep_unchanged(old_personas, rel_now)
                print(f"kept {len(kept)} old profiles; new profiles for the release suppliers: {' '.join(redone)}",
                      flush=True)  # fmt: skip
            changes = persona_diff(old_personas, json.loads(PERSONAS.read_text(encoding="utf-8")))
            print(f"persona changes the policy reads ({len(changes)}):", flush=True)
            for line in changes:
                print(f"  {line}", flush=True)
        if name == "parley data":
            if not ok[name]:
                print("data download failed: stopping before prepare", flush=True)
                break
            if a.release_from:
                src, pkg = Path(a.release_from).resolve(), package_dir()
                dst = (pkg.parent if pkg else AGENT / "data") / "release-sunday"
                if src != dst.resolve():
                    shutil.rmtree(dst, ignore_errors=True)
                    shutil.copytree(src, dst)
                print(f"rehearsal: release copied from {src} to {dst}", flush=True)
            rel = release_dir(package_dir())
            rel_now = rel
            if rel:
                man = write_manifest(rel, out)
                print(
                    f"release manifest: sha256 {man['sha256']}, {len(man['files'])} files",
                    flush=True,
                )
    after = snapshot(env)
    (out / f"data-after-{stamp()}.json").write_text(
        json.dumps(after, indent=1), encoding="utf-8"
    )
    print("\n== what changed")
    for line in diff(before, after):
        print(f"  {line}")
    print("\n== steps")
    for name, good in ok.items():
        print(f"  [{'PASS' if good else 'FAIL'}] {name}")
    if man:
        print(
            f"  [PASS] release recorded: sha256 {man['sha256'][:16]}..., {len(man['files'])} files"
        )
        for f in man["files"][:40]:
            print(f"         {f['path']}  {f['bytes']} B  {f['sha256'][:12]}")
    if not after.get("release"):
        bar = "!" * 78
        print(f"\n{bar}\n!!! SUBMIT FALLBACK: no Sunday release at {datetime.now():%H:%M} - submit the fallback build "
              f"(tag {a.fallback} = {fallback_sha(a.fallback)}), not the candidate. See ops/README.md\n{bar}")  # fmt: skip
        sys.exit(2)
    sys.exit(0 if all(ok.values()) else 1)


if __name__ == "__main__":
    main()
