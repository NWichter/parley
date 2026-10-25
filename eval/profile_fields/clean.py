"""Clean one model's extraction: keep only values with verified quotes; canonical keys for comparison."""
import json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import lib

INPUT_GROUP = {"copper": "copper", "nickel": "nickel_alloy", "alloy": "nickel_alloy", "nickel and alloys": "nickel_alloy",
               "electricity": "electricity", "power": "electricity", "energy": "electricity", "gas": "gas",
               "steel": "steel", "bar steel": "bar_steel", "bar": "bar_steel", "aluminium": "aluminium", "aluminum": "aluminium",
               "material": "material"}
ENUMS = {"cash_need": {"weak", "strong"}, "decision_maker": {"owner", "kam", "sales_director", "inside_sales", "export"},
         "patience": {"low", "med", "high"}, "volume_posture": {"accepts_below_cost", "wants_volume"}}
TRUE_ONLY = {"uninformed_owner", "past_goodwill_claim", "open_calculation", "margin_floor", "account_deprioritised"}

def group(inp):
    s = (inp or "").lower().strip()
    if s in INPUT_GROUP: return INPUT_GROUP[s]
    for k, v in INPUT_GROUP.items():
        if k in s: return v
    return s or "material"

def ev_ok(sid, evs, stats):
    good = []
    for e in evs or []:
        if isinstance(e, dict) and lib.verify(sid, e):
            good.append({"source": e["source"], "quote": e["quote"].strip()})
        else:
            stats["unverified"] += 1
    stats["quotes"] += len(evs or [])
    return good

def scalar_key(f, v):
    if v is None: return None
    if f == "approval_threshold_pct":
        try: return round(float(v), 2)
        except Exception: return None
    if f == "approval_cycle_days":
        try: return int(v)
        except Exception: return None
    if f == "approval_cycle": return "stated"
    if f == "price_freeze":
        return "freeze@" + str((v or {}).get("until") if isinstance(v, dict) else v)
    if f == "patience" and v == "medium": v = "med"
    if f in ENUMS: return v if v in ENUMS[f] else None
    if f in TRUE_ONLY: return True if v is True else None
    if f == "capacity_tight": return v if isinstance(v, bool) else None
    return v

def clean(sid, data):
    stats = {"quotes": 0, "unverified": 0, "dropped_values": 0}
    out = {}
    for f in lib.SCALARS:
        x = data.get(f) or {}
        if not isinstance(x, dict): x = {"value": x}
        v = x.get("value")
        if f == "patience" and v == "medium": v = "med"
        key = scalar_key(f, v)
        ev = ev_ok(sid, x.get("evidence"), stats) if key is not None else []
        if key is not None and not ev:
            stats["dropped_values"] += 1; key = None
        if key is None:
            out[f] = {"value": None, "evidence": [], "confidence": None, "key": None}
        else:
            out[f] = {"value": v, "evidence": ev, "confidence": x.get("confidence") or "medium", "key": key}
    for f in ("preferred_levers", "avoid_levers"):
        items = {}
        for it in data.get(f) or []:
            lv = (it or {}).get("lever")
            if lv not in lib.LEVERS: continue
            ev = ev_ok(sid, it.get("evidence"), stats)
            if not ev: stats["dropped_values"] += 1; continue
            if lv in items: items[lv]["evidence"] += ev; continue
            items[lv] = {"lever": lv, "stance": it.get("stance"), "condition": it.get("condition"), "evidence": ev,
                         "confidence": it.get("confidence") or "medium"}
        out[f] = items
    items = {}
    for it in data.get("hedged_inputs") or []:
        g = group((it or {}).get("input"))
        ev = ev_ok(sid, it.get("evidence"), stats)
        if not ev: stats["dropped_values"] += 1; continue
        if g in items:
            items[g]["evidence"] += ev
            if not items[g]["until"] and it.get("until"): items[g]["until"] = it.get("until")
            continue
        items[g] = {"input": g, "until": it.get("until"), "evidence": ev, "confidence": it.get("confidence") or "medium"}
    out["hedged_inputs"] = items
    return out, stats

def load(model, sid):
    f = lib.HERE / "out" / model / (sid.replace(":", "_") + ".json")
    if not f.exists(): return None
    d = json.loads(f.read_text(encoding="utf-8"))["data"]
    return d if "_error" not in d else None
