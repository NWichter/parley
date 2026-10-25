import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import lib, merge
F = lib.SCALARS + ["preferred_levers", "avoid_levers", "hedged_inputs"]
rows = {f: {"n": 0, "agree": 0, "agree_nonnull": 0, "any_nonnull": 0, "cx": 0, "gx": 0, "all3": 0, "n3": 0} for f in F}
Q = {"total": 0, "verified": 0}
item = {f: [0, 0] for f in ("preferred_levers", "avoid_levers", "hedged_inputs")}
for sid in sorted(lib.B):
    res = merge.cleaned(sid)
    for m in res:
        Q["total"] += res[m][1]["quotes"]; Q["verified"] += res[m][1]["quotes"] - res[m][1]["unverified"]
    k = {m: merge.keys(res[m][0]) for m in res}
    if "gemini" not in k: continue
    for f in F:
        r = rows[f]; c, g = json.dumps(k["claude"][f]), json.dumps(k["gemini"][f])
        nn = lambda v: v not in ("null", "[]")
        r["n"] += 1; r["agree"] += c == g
        r["any_nonnull"] += nn(c) or nn(g); r["agree_nonnull"] += (c == g) and nn(c)
        if "codex" in k:
            x = json.dumps(k["codex"][f]); r["n3"] += 1
            r["cx"] += c == x; r["gx"] += g == x; r["all3"] += c == g == x
    for f in item:
        a, b = set(k["claude"][f]), set(k["gemini"][f])
        item[f][0] += len(a & b); item[f][1] += len(a | b)
json.dump({"fields": rows, "items": item, "quotes": Q}, open(lib.HERE / "stats.json", "w"), indent=1)
print(f"{'field':24} agree  nonnull-agree/any  | codex10: C=X G=X all3")
for f, r in rows.items():
    print(f"{f:24} {r['agree']}/{r['n']}  {r['agree_nonnull']}/{r['any_nonnull']}   | {r['cx']}/{r['n3']} {r['gx']}/{r['n3']} {r['all3']}/{r['n3']}")
for f, (i, u) in item.items(): print(f, "item jaccard", i, "/", u)
