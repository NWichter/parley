import csv, hashlib, json, random, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import lib, clean

MODELS = ["claude", "gemini", "codex"]
RANK = {"high": 3, "medium": 2, "low": 1}
SERIES_MAP = {"copper": ["CU-NOTATION", "WB-CU"], "nickel_alloy": ["AS-14404", "AS-14301", "WB-NI"],
              "electricity": ["PPI-D351", "PPI-D35", "SMARD-DA"], "gas": ["WB-NGEU", "PPI-D35"],
              "steel": ["PPI-C241", "WB-FEORE"], "bar_steel": ["PPI-C2431", "PPI-C241"], "aluminium": ["WB-AL"]}
D = lib.package() / "data"
src = list(csv.DictReader(open(D / "sourcing.csv", encoding="utf-8")))
drv = list(csv.DictReader(open(D / "cost_drivers.csv", encoding="utf-8")))
def drivers(sid):
    parts = {r["part_id"] for r in src if r["supplier_id"] == sid}
    out = {}
    for r in drv:
        if r["part_id"] in parts:
            out[r["series_id"]] = max(out.get(r["series_id"], 0), float(r["weight"]))
    return out

def cleaned(sid):
    res = {}
    for m in MODELS:
        d = clean.load(m, sid)
        if d is not None:
            res[m] = clean.clean(sid, d)
    return res

def keys(c):
    k = {f: c[f]["key"] for f in lib.SCALARS}
    for f in ("preferred_levers", "avoid_levers"):
        k[f] = tuple(sorted(c[f]))
    k["hedged_inputs"] = tuple(sorted(c["hedged_inputs"]))
    return k

def disputes(sid, res):
    out = []
    ms = list(res)
    for f in lib.SCALARS:
        ks = {m: res[m][0][f]["key"] for m in ms}
        if len(set(map(json.dumps, ks.values()))) > 1:
            cands = []
            for m in ms:
                c = res[m][0][f]
                if c["key"] is not None and all(json.dumps(c["key"]) != json.dumps(x["key"]) for x in cands):
                    cands.append({"key": c["key"], "value": c["value"], "evidence": c["evidence"]})
            out.append({"id": f, "field": f, "kind": "scalar", "candidates": cands})
    for f in ("preferred_levers", "avoid_levers", "hedged_inputs"):
        allitems = set().union(*[set(res[m][0][f]) for m in ms])
        for it in sorted(allitems):
            have = [m for m in ms if it in res[m][0][f]]
            if len(have) != len(ms):
                ex = res[have[0]][0][f][it]
                out.append({"id": f"{f}:{it}", "field": f, "kind": "item", "item": it,
                            "candidates": [{"value": {k: v for k, v in ex.items() if k != "confidence"}, "evidence": ex["evidence"]}]})
    return out

ADJ = """You adjudicate disagreements between independent extractors of supplier negotiation fields.
Field definitions and rules (the extractors had exactly these):
<<<
{prompt}
>>>
For each dispute below, decide strictly by the sources and the definitions.
- Scalar dispute: candidates are labelled; pick the label whose value the sources support under the definition, or "null" if none is supported.
- Item dispute: one extractor listed the item, another did not; answer "keep" if the sources support the item under the definition, else "drop".
Reply with one JSON object only: {{"<dispute id>": {{"choice": "<label>|null|keep|drop", "reason": "<= 25 words"}}, ...}}"""

def adjudicate(sid, ds, client):
    f = lib.HERE / "out" / "adjud" / (sid.replace(":", "_") + ".json")
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    rnd = random.Random(int(hashlib.md5(sid.encode()).hexdigest(), 16))
    lines, labels = [], {}
    for d in ds:
        if d["kind"] == "scalar":
            cands = d["candidates"][:]
            rnd.shuffle(cands)
            labels[d["id"]] = {}
            opts = []
            for i, c in enumerate(cands):
                L = "ABC"[i]
                labels[d["id"]][L] = c["key"]
                opts.append(f"  {L}: value={json.dumps(c['value'], ensure_ascii=False)} evidence={json.dumps(c['evidence'], ensure_ascii=False)}")
            opts.append("  null: no value is supported")
            lines.append(f"DISPUTE {d['id']} (scalar field {d['field']}):\n" + "\n".join(opts))
        else:
            c = d["candidates"][0]
            lines.append(f"DISPUTE {d['id']} (list field {d['field']}): item={json.dumps(c['value'], ensure_ascii=False)}")
    user = f"Supplier {sid}: {lib.B[sid]['name']}\n\nSOURCES\n\n{lib.render(sid)}\n\nDISPUTES\n\n" + "\n\n".join(lines)
    r = client.messages.create(model="claude-opus-5-5", max_tokens=8000, system=ADJ.format(prompt=lib.PROMPT),
                               messages=[{"role": "user", "content": user}], output_config={"effort": "high"})
    text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
    out = {"choices": lib.parse_json(text), "labels": labels, "usage": {"in": r.usage.input_tokens, "out": r.usage.output_tokens}}
    f.parent.mkdir(exist_ok=True)
    f.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out

if __name__ == "__main__" and sys.argv[1] == "adjudicate":
    import anthropic
    client = anthropic.Anthropic(api_key=lib.env_key("ANTHROPIC_API_KEY_2"), max_retries=3, timeout=300)
    def one(sid):
        res = cleaned(sid)
        ds = disputes(sid, res)
        if not ds: return sid, 0
        adjudicate(sid, ds, client)
        return sid, len(ds)
    with ThreadPoolExecutor(8) as p:
        for sid, n in p.map(one, sorted(lib.B)):
            print(sid, n, flush=True)
