"""Compare persona-bank runs side by side, e.g. the deterministic bank against LLM-voiced suppliers.

    uv run eval/compare.py det=eval/results/<ts1> sonnet=eval/results/<ts2> [--out report.md]

Prints, per run and agent: mean score per persona, outcomes, spend, and for LLM runs what the
supplier's reader picked up in the buyer's e-mails (tone flags, strongest arguments, quotes), plus
the buyer's greeting lines (to catch a bad salutation).
"""

import argparse
import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

PERSONAS = ["mirror", "holder", "floor_stop", "short_fuse", "auction", "index_lover", "cash_constrained",
            "dup_punisher", "announced_steps"]  # fmt: skip
TONE = ("insult", "condescending", "lowball_tone", "appreciation", "payment_promise", "volume_commitment", "deadline")
BUYER = re.compile(r"### BUYER\n```\n(.*?)```", re.S)


def load(spec):
    """`dir` or `dirA+dirB` (a run completed in a second run): rows by game, the first folder wins."""
    rows, seen, usage = [], set(), {}
    for path in spec.split("+"):
        for x in (Path(path) / "results.jsonl").read_text(encoding="utf-8").splitlines():
            r = json.loads(x) if x.strip() else None
            if r and r["game"] not in seen:
                seen.add(r["game"])
                r["_dir"] = path
                rows.append(r)
        u = Path(path) / "usage.json"
        if u.is_file() and not usage:
            usage = json.loads(u.read_text(encoding="utf-8"))
    return rows, usage


def mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.mean(xs) if xs else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+", help="label=<results dir>")
    ap.add_argument("--out")
    a = ap.parse_args()
    runs = {}
    for spec in a.runs:
        label, _, path = spec.partition("=")
        runs[label] = ([Path(x) for x in path.split("+")], *load(path))
    cols = [(lab, ag) for lab, (_, rows, _) in runs.items() for ag in sorted({r["agent"] for r in rows})]
    md = ["## Mean score per persona", "", "| persona | " + " | ".join(f"{lab}/{ag}" for lab, ag in cols) + " |",
          "|---|" + "---:|" * len(cols)]  # fmt: skip
    cell = defaultdict(list)
    for lab, (_, rows, _) in runs.items():
        for r in rows:
            cell[(lab, r["agent"], r["persona"])].append(r["score"])
            cell[(lab, r["agent"], "all")].append(r["score"])
    for p in [*PERSONAS, "all"]:
        if not any(cell[(lab, ag, p)] for lab, ag in cols):
            continue
        vals = [f"{mean(cell[(lab, ag, p)]):.3f}" if cell[(lab, ag, p)] else "-" for lab, ag in cols]
        md.append(f"| {'**all**' if p == 'all' else p} | " + " | ".join(vals) + " |")
    md += ["", "| run/agent | games | deals | supplier walked | buyer walked | rounds | agent $ | supplier $ | $/game |",
           "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]  # fmt: skip
    for lab, ag in cols:
        rows = [r for r in runs[lab][1] if r["agent"] == ag]
        n = len(rows)
        sup = sum((r.get("supplier_llm") or {}).get("usd", 0) for r in rows)
        agu = sum(r.get("agent_usd") or 0 for r in rows)
        md.append(f"| {lab}/{ag} | {n} | {sum(r['deal'] for r in rows)} | {sum('supplier_walked' in (r.get('failure') or '') for r in rows)} | "
                  f"{sum('buyer_walked' in (r.get('failure') or '') for r in rows)} | {mean([r.get('rounds') for r in rows]):.1f} | "
                  f"{agu:.3f} | {sup:.3f} | {(agu + sup) / max(n, 1):.4f} |")  # fmt: skip
    for lab, (path, rows, usage) in runs.items():
        if not usage.get("supplier_llm"):
            continue
        u = usage["supplier_llm"]
        md += ["", f"## Reader in `{lab}` (writer {u['writer']}, reader {u['reader']})", "",
               f"Calls {u['calls']}, failed {u['failures']}, fallbacks {u['fallbacks']}.", ""]  # fmt: skip
        for ag in sorted({r["agent"] for r in rows}):
            notes = [x for r in rows if r["agent"] == ag for x in r.get("reader") or []]
            reads = [x["reader"] for x in notes if x.get("reader")]
            effects = [x["effect"] for x in notes if x.get("effect") and "bonus" in x["effect"]]
            n = max(len(reads), 1)
            flags = {t: sum(bool(rd.get(t)) for rd in reads) for t in TONE}
            walk = Counter(rd.get("walk_threat", 0) or 0 for rd in reads)
            strong = Counter((rd.get("strongest_ref") or "none").split(":")[0] for rd in reads)
            uncited = sum(
                1 for rd in reads for c in rd.get("claims") or [] if c.get("has_figure") and not c.get("cited")
            )
            claims = sum(len(rd.get("claims") or []) for rd in reads)
            bonus = [e["bonus"] for e in effects if e["bonus"]]
            cuts = [e["cut"] for e in effects if e["cut"]]
            md += [f"### {ag}: {len(reads)} buyer e-mails read", "",
                   "Tone flags: " + ", ".join(f"{t} {flags[t]} ({flags[t] / n:.0%})" for t in TONE)
                   + f"; walk threat 1: {walk[1]}, 2: {walk[2]}.",
                   f"Claims extracted {claims}, of them with a figure but no data ID: {uncited}.",
                   "Strongest argument by data type: " + ", ".join(f"{k} {v}" for k, v in strong.most_common()) + ".",
                   f"Reader bonus applied in {len(bonus)} rounds (mean {mean(bonus) if bonus else 0:.3f} rooms); "
                   f"irritation cut in {len(cuts)} rounds; irritation walk-aways "
                   f"{sum(1 for r in rows if r['agent'] == ag and 'tone of your messages' in _supplier_text(path, r))}.", ""]  # fmt: skip
            quotes = Counter(rd.get("react_to") for rd in reads if rd.get("react_to"))
            md += ["What the supplier reacted to most (reader's `react_to`, most frequent first):", ""]
            md += [f'- ({c}x) "{q}"' for q, c in quotes.most_common(8)]
            odd = Counter()
            for rd in reads:
                for t in ("insult", "condescending", "lowball_tone"):
                    if rd.get(t) and rd.get("react_to"):
                        odd[f'{t}: "{rd["react_to"]}"'] += 1
            if odd:
                md += ["", "Flagged as negative tone:", ""] + [f"- ({c}x) {q}" for q, c in odd.most_common(8)]
            md.append("")
    greet = Counter()
    for lab, (path, rows, _) in runs.items():
        for f in [f for d in path for f in (d / "transcripts").glob("*.md")]:
            for body in BUYER.findall(f.read_text(encoding="utf-8")):
                first = next((x.strip() for x in body.splitlines() if x.strip()), "")
                greet[re.sub(r"Dear [A-ZÄÖÜ][\w.-]+ [A-ZÄÖÜ][\w.-]+,", "Dear <First Last>,", first)] += 1
    md += ["## Buyer greeting lines (all runs)", ""] + [f"- ({c}x) `{g}`" for g, c in greet.most_common(15)]
    text = "\n".join(md) + "\n"
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    print(text)


def _supplier_text(path, row):
    f = Path(row.get("_dir") or path[0]) / "transcripts" / f"{row['game']}.md"
    return f.read_text(encoding="utf-8") if f.is_file() else ""


if __name__ == "__main__":
    main()
