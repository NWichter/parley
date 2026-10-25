import hashlib, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import lib, merge
from merge import RANK

def rel(sid, ev):
    out, seen = [], set()
    for e in ev:
        k = (e["source"], lib.norm(e["quote"]))
        if k in seen: continue
        seen.add(k)
        u = next(u for u in lib.B[sid]["units"] if u["id"] == e["source"])
        out.append({"source": e["source"], "quote": e["quote"], "kind": u["kind"], "date": u["date"] or None, "release": u["release"]})
    return out

def minconf(cs):
    cs = [c for c in cs if c]
    return min(cs, key=lambda c: RANK.get(c, 2)) if cs else None

def build(sid):
    res = merge.cleaned(sid)
    ms = list(res)
    adj = None
    af = lib.HERE / "out" / "adjud" / (sid.replace(":", "_") + ".json")
    if af.exists(): adj = json.loads(af.read_text(encoding="utf-8"))
    fields, summary = {}, {}
    for f in lib.SCALARS:
        per = {m: res[m][0][f] for m in ms}
        ks = {m: per[m]["key"] for m in ms}
        agreement = {m: ks[m] for m in ms}
        if len(set(map(json.dumps, ks.values()))) == 1:
            k = ks["claude"]
            status = "agree" if k is not None else "agree_null"
            conf = minconf([per[m]["confidence"] for m in ms]) if k is not None else None
        else:
            ch = (adj or {}).get("choices", {}).get(f, {}).get("choice") if adj else None
            lab = (adj or {}).get("labels", {}).get(f, {})
            if ch in lab: k = lab[ch]
            elif ch in (None, "null") and adj: k = None
            else: k = ks["claude"]
            status = "resolved" if k is not None else "resolved_null"
            conf = "low" if k is not None else None
            agreement["reason"] = (adj or {}).get("choices", {}).get(f, {}).get("reason")
        agreement["status"] = status
        src_m = [m for m in ms if json.dumps(ks[m]) == json.dumps(k)] if k is not None else []
        val = per[src_m[0]]["value"] if src_m else None
        if f == "patience" and val == "medium": val = "med"
        ev = rel(sid, [e for m in src_m for e in per[m]["evidence"]])
        fields[f] = {"value": val, "evidence": ev, "confidence": conf, "agreement": agreement}
        summary[f] = val
    for f in ("preferred_levers", "avoid_levers", "hedged_inputs"):
        allitems = sorted(set().union(*[set(res[m][0][f]) for m in ms]))
        items = []
        for it in allitems:
            have = [m for m in ms if it in res[m][0][f]]
            if len(have) == len(ms):
                status, conf, keep = "agree", minconf([res[m][0][f][it]["confidence"] for m in ms]), True
                reason = None
            else:
                c = (adj or {}).get("choices", {}).get(f"{f}:{it}", {})
                keep = c.get("choice") == "keep" if adj else ("claude" in have)
                status, conf, reason = ("resolved_keep" if keep else "resolved_drop"), "low", c.get("reason")
            base = dict(res[have[0]][0][f][it])
            base.pop("confidence", None)
            ev = rel(sid, [e for m in have for e in res[m][0][f][it]["evidence"]])
            entry = {**base, "evidence": ev, "confidence": conf if keep else None,
                     "agreement": {"found_by": have, "of": ms, "status": status, **({"reason": reason} if reason else {})}}
            if f == "hedged_inputs":
                dr = merge.drivers(sid)
                if it == "material":
                    ser = sorted(s for s, w in dr.items() if w >= 0.2 and not s.startswith(("LCI", "ECB")))
                    entry["series_from"] = "supplier part cost drivers, weight >= 0.2, without labour/FX"
                else:
                    table = merge.SERIES_MAP.get(it, [])
                    inter = [s for s in table if s in dr]
                    ser = inter or table
                    entry["series_from"] = "keyword table ∩ supplier part cost drivers" if inter else "keyword table"
                entry["series"] = ser
            if keep: items.append(entry)
            else: fields.setdefault("_dropped", []).append({"field": f, **entry})
        fields[f] = items
        summary[f] = sorted({x["lever"] for x in items}) if f != "hedged_inputs" else [
            {"input": x["input"], "until": x["until"], "series": x["series"]} for x in items]
    return {"name": lib.B[sid]["name"], "summary": summary, "fields": fields, "models": ms}

if __name__ == "__main__":
    out = {"_meta": {
        "schema": "profile-fields v1", "prompt_sha256_12": hashlib.sha256((lib.HERE / "prompt_v1.txt").read_bytes()).hexdigest()[:12],
        "data": "package-v1", "release_sources": [],
        "sources": "Tacto data package only: suppliers, supplier_intel, contract_clauses, documents+correspondence, news, quotes, negotiation_history notes, sourcing, cost_drivers. No replays, practice runs or outcome data.",
        "extractor": "claude-opus-5-5 (effort high)", "cross_check": "gemini-4-argon-high via agy (all 46), codex exec (10)",
        "adjudicator": "claude-opus-5-5, blind labels, only on disagreements; resolved values get confidence low",
        "null_means": "no verified evidence in the package; not 'false'",
        "note": "Values are data, not rules. The policy maps each field the same way for every supplier.",
        "created": "2026-10-24"}}
    for sid in sorted(lib.B):
        out[sid] = build(sid)
    Path(sys.argv[1]).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("written", len(out) - 1)
