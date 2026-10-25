"""De-anonymise the judges' ratings and aggregate them with the generation log.

    python eval/writer_ab/aggregate.py   -> out/summary.json, printed tables
"""

import json
import statistics
from collections import defaultdict

from common import OUT

CRIT = ["credibility", "clarity", "tone", "persuasiveness", "fit"]
NAMES = ["opus", "sonnet", "haiku", "gpt", "gemini"]


def pct(v, q):
    v = sorted(x for x in v if x is not None)
    return round(v[min(len(v) - 1, int(q * len(v)))], 2) if v else None


def judgements():
    for d in sorted(OUT.glob("judge_*")):
        for f in sorted(d.glob("*.json")):
            j = json.loads(f.read_text(encoding="utf-8"))
            for x in j if isinstance(j, list) else [j]:
                yield d.name[6:], x


def main():
    key = json.loads((OUT / "key.json").read_text(encoding="utf-8"))
    mails = json.loads((OUT / "mails.json").read_text(encoding="utf-8"))
    score = defaultdict(lambda: defaultdict(list))
    per_judge = defaultdict(lambda: defaultdict(list))
    halluc, best, worst = defaultdict(int), defaultdict(int), defaultdict(int)
    flagged = defaultdict(lambda: defaultdict(int))
    hlist = defaultdict(list)
    used = {(g["sit"], g["name"]): g["used_llm"] for g in mails if g["rep"] == 0}
    kind = defaultdict(list)
    for judge, x in judgements():
        k = key[str(x["situation"])]
        for L, r in x["ratings"].items():
            m = k[L]
            vals = [float(r[c]) for c in CRIT]
            for c, v in zip(CRIT, vals):
                score[m][c].append(v)
            per_judge[judge][m].append(statistics.mean(vals))
            kind[("llm" if used[(x["situation"], m)] else "template", m)].append(statistics.mean(vals))
            h = [s for s in r.get("hallucinations") or [] if s and s.strip()]
            halluc[m] += len(h)
            if h:
                flagged[m][x["situation"]] += 1
                hlist[m] += [(judge, x["situation"], s) for s in h]
        if x.get("best") in k:
            best[k[x["best"]]] += 1
        if x.get("worst") in k:
            worst[k[x["worst"]]] += 1
    rows = []
    for m in NAMES:
        gen = [g for g in mails if g["name"] == m]
        ok = [g for g in gen if g["used_llm"]]
        reasons = defaultdict(int)
        for g in gen:
            if not g["used_llm"]:
                reasons[g["why"]] += 1
        crit = {c: round(statistics.mean(score[m][c]), 2) for c in CRIT}
        rows.append({
            "model": m, **crit, "overall": round(statistics.mean(crit.values()), 2),
            "per_judge": {j: round(statistics.mean(v[m]), 2) for j, v in per_judge.items()},
            "hallucinations": halluc[m], "situations_flagged_by_2plus": sum(1 for v in flagged[m].values() if v >= 2),
            "best": best[m], "worst": worst[m],
            "fallback_rate": round(1 - len(ok) / len(gen), 3), "fallbacks": dict(reasons), "mails": len(gen),
            "p50_s": pct([g["model_s"] for g in gen], 0.5), "p95_s": pct([g["model_s"] for g in gen], 0.95),
            "usd_per_mail": round(statistics.mean(g["usd"] for g in gen), 5),
        })
    rows.sort(key=lambda r: -r["overall"])
    (OUT / "summary.json").write_text(json.dumps({"rows": rows, "hallucinations": hlist}, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    for r in rows:
        print(json.dumps(r))
    by_kind = {f"{k}/{m}": (round(statistics.mean(v), 2), len(v)) for (k, m), v in sorted(kind.items())}
    allk = defaultdict(list)
    for (k, _m), v in kind.items():
        allk[k] += v
    by_kind.update({k: (round(statistics.mean(v), 2), len(v)) for k, v in allk.items()})
    print(json.dumps(by_kind))
    s = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
    s["by_kind"] = by_kind
    (OUT / "summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
