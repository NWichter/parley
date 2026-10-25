"""Persona floors: the strong bank personas must not lose ground.

Reads a bank results.jsonl. Paired mode (two agents in the same run, the normal merge gate):
  - a strong persona's mean drops by more than 0.005 -> FAIL
  - a game of a strong persona drops by more than 0.05, or more than 2 games of one strong persona
    drop by more than 0.02 -> FAIL (--allow-game-drops makes these a warning, name the reason in MERGES.md)
  - a new zero game in any persona (above 0 before, 0 after) -> FAIL
Single mode (one agent): every strong persona's mean must be at least floors.json - 0.01.

usage:
  python eval/guards/persona_floors.py eval/results/<ts>/results.jsonl --base main --cand cand
  python eval/guards/persona_floors.py eval/results/<ts>/results.jsonl --agent parley
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
MEAN_DROP, GAME_DROP, SMALL_DROP, SMALL_COUNT, FLOOR_TOL = 0.005, 0.05, 0.02, 2, 0.01


def load(path):
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def key(r):
    return (r["persona"], r["case_id"], r["seed"])


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("results")
    ap.add_argument("--base")
    ap.add_argument("--cand")
    ap.add_argument("--agent", help="single mode: check this agent against floors.json")
    ap.add_argument("--floors", default=str(HERE / "floors.json"))
    ap.add_argument("--allow-game-drops", action="store_true")
    a = ap.parse_args(argv)
    floors = json.loads(Path(a.floors).read_text(encoding="utf-8"))["personas"]
    rows = load(a.results)
    by = defaultdict(dict)
    for r in rows:
        by[r["agent"]][key(r)] = r["score"] or 0.0
    fails, warns = [], []
    if a.agent or not (a.base and a.cand):
        agent = a.agent or next(iter(by))
        for p, floor in floors.items():
            v = [s for (pp, _, _), s in by[agent].items() if pp == p]
            if not v:
                continue
            mean = sum(v) / len(v)
            line = f"{p:17} {mean:.4f} floor {floor:.4f}"
            print(line)
            if mean < floor - FLOOR_TOL:
                fails.append(line)
    else:
        b, c = by[a.base], by[a.cand]
        common = sorted(set(b) & set(c))
        if not common:
            print(
                f"no paired games for {a.base} / {a.cand}; agents in file: {sorted(by)}"
            )
            return 3
        for p in floors:
            ks = [k for k in common if k[0] == p]
            if not ks:
                continue
            mb, mc = sum(b[k] for k in ks) / len(ks), sum(c[k] for k in ks) / len(ks)
            drops = sorted(((b[k] - c[k]), k) for k in ks if b[k] - c[k] > SMALL_DROP)
            big = [d for d in drops if d[0] > GAME_DROP]
            line = f"{p:17} {mb:.4f} -> {mc:.4f} ({mc - mb:+.4f}), games down >{SMALL_DROP}: {len(drops)}"
            print(line)
            if mc < mb - MEAN_DROP:
                fails.append(f"{p}: mean {mc - mb:+.4f}")
            for d, k in big:
                (warns if a.allow_game_drops else fails).append(
                    f"{p} {k[1]} s{k[2]}: {b[k]:.3f} -> {c[k]:.3f}"
                )
            if len(drops) > SMALL_COUNT:
                (warns if a.allow_game_drops else fails).append(
                    f"{p}: {len(drops)} games down by more than {SMALL_DROP}"
                )
        zeros = [k for k in common if b[k] > 0 and c[k] == 0]
        for k in zeros:
            fails.append(f"new zero: {k[0]} {k[1]} s{k[2]} ({b[k]:.3f} -> 0)")
        allb, allc = (
            sum(b[k] for k in common) / len(common),
            sum(c[k] for k in common) / len(common),
        )
        print(
            f"{'all':17} {allb:.4f} -> {allc:.4f} ({allc - allb:+.4f}), {len(common)} paired games"
        )
    for w in warns:
        print("WARN", w)
    for f in fails:
        print("FAIL", f)
    print(
        "FAIL: a strong persona lost ground" if fails else "PASS: strong personas hold"
    )
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
