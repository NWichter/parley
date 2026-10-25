"""`parley` command line: data, me, dossier, play, run, prepare."""

import argparse
import io
import json
import logging
import os
import statistics
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import llm as llm_mod
from .dossier import ROOT, Dossier, dump, load_package
from .kit.client import ArenaClient, ArenaError
from .runner import CaseRunner, load_personas, play_waves, summarise

# The supervisor (ops/final.py) stops restarting on this exit code: a restart cannot fix missing data.
EXIT_NO_DATA = 78
# Arena sessions open at once per team: leave one free for a resume or a case that waits for a slot.
FINAL_PARALLEL = 9

ENV_KEYS = (
    "ARENA_URL",
    "ARENA_API_KEY",
    "ARENA_DATA",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "PLAN_MODEL",
    "WRITE_MODEL",
    "ANALYST_MODEL",
)


def load_env():
    """KEY=VALUE lines from the repo-root .env (real environment variables win)."""
    for path in (ROOT.parent / ".env", ROOT / ".env"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("\"'")
            if sep and value and key in ENV_KEYS:
                os.environ.setdefault(key, value)
    os.environ.setdefault("ARENA_URL", "https://arena.tacto.ai")


def client():
    return ArenaClient()


def practice_used(c):
    try:
        return c.me().get("practice", {}).get("used")
    except ArenaError:
        return None


def guard_practice(case_ids, allow):
    practice = [c for c in case_ids if c.startswith("case:P")]
    if practice and not allow:
        sys.exit(f"{', '.join(practice)}: practice cases count against the team quota. Pass --allow-practice.")


def mode_of(args):
    if getattr(args, "no_llm", False):
        return "nollm"
    return getattr(args, "mode", None) or "full"


def make_llm(args):
    if mode_of(args) == "nollm":
        return None
    llm = llm_mod.from_env()
    if llm is None:
        logging.getLogger("parley").warning("no ANTHROPIC_API_KEY: running without a model")
    return llm


def cmd_play(args):
    guard_practice([args.case_id], args.allow_practice)
    c, pkg, llm = client(), load_package(args.data), make_llm(args)
    before = practice_used(c)
    deadline = float(os.environ["PARLEY_DEADLINE"]) if os.environ.get("PARLEY_DEADLINE") else None
    runner = CaseRunner(c, args.case_id, pkg, llm, deadline=deadline, personas=load_personas(pkg), mode=mode_of(args))
    try:
        result = runner.play()
    except ArenaError as e:
        sys.exit(str(e))
    print(json.dumps(summarise(result), indent=2))
    if runner.session_id:
        state = c.session(runner.session_id)
        if state.get("replay_url"):
            print(f"replay: {os.environ['ARENA_URL'].rstrip('/')}{state['replay_url']}")
        print(f"journal: {runner.journal.path}")
    if llm:
        print("llm:", json.dumps(llm.usage.summary()))
    after = practice_used(c)
    if before is not None:
        print(f"practice sessions used: {before} -> {after}")


def check_data(c, pkg):
    """The final must not run on missing or stale data: no package, or a public Sunday release that
    is not unpacked next to it, stops the runner before the first wave."""
    if pkg is None:
        print("NO DATA PACKAGE: run `uv run parley data` first (the supervisor will not restart)", file=sys.stderr, flush=True)
        sys.exit(EXIT_NO_DATA)
    try:
        release = c.downloads().get("release-sunday") or {}
    except Exception as e:  # noqa: BLE001 - a restart under load must not die on this check
        logging.getLogger("parley").warning("cannot check the data downloads (%s): going on with %s", e, pkg.root)
        release = {}
    if release.get("url") and not getattr(pkg, "release", None):
        sys.exit("the Sunday release is public but not loaded: run `uv run parley data`, then `uv run parley prepare`")
    print(f"data: {pkg.root} ({len(pkg.facts)} facts){' + Sunday release' if getattr(pkg, 'release', None) else ''}")


def cmd_run(args):
    c, llm = client(), make_llm(args)
    try:
        pkg = load_package(args.data)
    except (OSError, ValueError) as e:
        if args.set != "final":
            raise
        print(f"DATA PACKAGE UNREADABLE ({e}): run `uv run parley data` (the supervisor will not restart)", file=sys.stderr,
              flush=True)
        sys.exit(EXIT_NO_DATA)
    personas = load_personas(pkg)
    before = practice_used(c)
    if args.set == "final":
        check_data(c, pkg)

        def new_runner(case, deadline):
            r = CaseRunner(c, case["case_id"], pkg, llm, case, deadline, personas, mode=mode_of(args))
            return r.play()

        out = play_waves(c, new_runner, args.waves, min(args.parallel, FINAL_PARALLEL))
    else:
        ids = args.cases or [x["case_id"] for x in c.cases(args.set) if x.get("hosted", True)]
        guard_practice(ids, args.allow_practice)
        if args.set == "practice" and not args.cases:
            sys.exit("practice: name the cases with --cases (no bulk runs)")

        def one(cid):
            try:
                return cid, CaseRunner(c, cid, pkg, llm, personas=personas, mode=mode_of(args)).play()
            except Exception as e:  # noqa: BLE001
                logging.getLogger("parley").exception("%s failed", cid)
                return cid, e

        with ThreadPoolExecutor(max_workers=min(args.parallel, 10)) as pool:
            out = dict(pool.map(one, ids))
    scores = []
    for cid, r in out.items():
        print(cid, r if isinstance(r, Exception) else json.dumps(summarise(r)))
        if isinstance(r, dict) and r.get("score") is not None:
            scores.append(r["score"])
    if scores:
        print(f"mean score: {statistics.mean(scores):.3f} over {len(scores)} cases")
    if llm:
        print("llm:", json.dumps(llm.usage.summary()))
    if before is not None:
        print(f"practice sessions used: {before} -> {practice_used(c)}")


def cmd_dossier(args):
    c, pkg = client(), load_package(args.data)
    cases = {x["case_id"]: x for x in c.cases(args.set)}
    print(dump(Dossier(cases[args.case_id], pkg)))


def cmd_data(args):
    c = client()
    out = Path(args.out)
    for name, info in c.downloads().items():
        if info.get("url"):
            zipfile.ZipFile(io.BytesIO(c.download(name))).extractall(out)
            print(f"{name}: {out / name}")
        else:
            print(f"{name}: public from {info.get('at')}")


def cmd_me(_args):
    print(json.dumps(client().me(), indent=2))


def cmd_prepare(args):
    from .prepare import prepare

    prepare(load_package(args.data), make_llm(args), force=args.force)


def main(argv=None):
    load_env()
    ap = argparse.ArgumentParser(prog="parley")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    data = {"default": os.environ.get("ARENA_DATA"), "help": "data package folder"}
    p = sub.add_parser("play", help="play one case")
    p.add_argument("case_id")
    p.add_argument("--no-llm", action="store_true", help="templates and rules only")
    p.add_argument("--mode", choices=["nollm", "hybrid", "full"], help="hybrid: the model reads, templates write")
    p.add_argument("--allow-practice", action="store_true")
    p.add_argument("--data", **data)
    r = sub.add_parser("run", help="play a set: sandbox, practice (named cases) or the final")
    r.add_argument("--set", default="sandbox", choices=["sandbox", "practice", "final"])
    r.add_argument("--cases", nargs="*")
    r.add_argument("--parallel", type=int, default=10)
    r.add_argument("--waves", type=int, default=5)
    r.add_argument("--no-llm", action="store_true")
    r.add_argument("--mode", choices=["nollm", "hybrid", "full"], help="hybrid: the model reads, templates write")
    r.add_argument("--allow-practice", action="store_true")
    r.add_argument("--data", **data)
    d = sub.add_parser("dossier", help="print the dossier of a case")
    d.add_argument("case_id")
    d.add_argument("--set", default="sandbox")
    d.add_argument("--data", **data)
    g = sub.add_parser("data", help="download the data package (and the Sunday release once public)")
    g.add_argument("--out", default=str(ROOT / "data"))
    sub.add_parser("me", help="team and practice quota")
    pr = sub.add_parser("prepare", help="write supplier profiles with the plan model (cached)")
    pr.add_argument("--force", action="store_true")
    pr.add_argument("--no-llm", action="store_true")
    pr.add_argument("--data", **data)
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(name)s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    {"play": cmd_play, "run": cmd_run, "dossier": cmd_dossier, "data": cmd_data, "me": cmd_me, "prepare": cmd_prepare}[
        args.cmd
    ](args)


if __name__ == "__main__":
    main()
