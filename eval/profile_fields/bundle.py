"""Build per-supplier source bundles from the Tacto data package only (no replays, no outcomes)."""
import csv, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = (os.environ.get("ARENA_DATA"), ROOT / "agent" / "data" / "package",
              ROOT / "internal" / "briefing" / "tacto" / "repo" / "data")
PKG = next((Path(c) for c in CANDIDATES if c and (Path(c) / "data" / "suppliers.csv").is_file()), None)
if PKG is None:
    sys.exit("no data package: run `parley data` or set ARENA_DATA")
D = PKG / "data"
def rows(t): return list(csv.DictReader(open(D / f"{t}.csv", encoding="utf-8")))
sup = {r["id"]: r for r in rows("suppliers") if r["detail"] == "full"}
intel = {r["supplier_id"]: r for r in rows("supplier_intel")}
clauses = rows("contract_clauses"); docs = rows("documents"); news = rows("news"); quotes = rows("quotes")
hist = rows("negotiation_history"); sourcing = rows("sourcing"); drivers = rows("cost_drivers")
parts = {r["id"]: r for r in rows("parts")}
series = {r["id"]: r["name"] for r in rows("index_series")}
def body(path):
    t = (PKG / path).read_text(encoding="utf-8")
    return t
# Sunday release (ARENA_RELEASE, or release-sunday inside/next to the package): supplier-specific
# letters and news only, marked release "sunday". The market memo is not supplier evidence.
REL = next((Path(c) for c in (os.environ.get("ARENA_RELEASE"), PKG / "release-sunday", PKG.parent / "release-sunday")
            if c and (Path(c) / "facts.json").is_file()), None)
def rel_rows(t):
    f = REL / "data" / f"{t}.csv" if REL else None
    return list(csv.DictReader(open(f, encoding="utf-8"))) if f and f.is_file() else []
rel_docs, rel_news = rel_rows("documents"), rel_rows("news")
out = {}
for sid, s in sup.items():
    units = []
    def add(uid, kind, date, text): units.append({"id": uid, "kind": kind, "date": date, "release": "package-v1", "text": text.strip()})
    add(sid, "vendor_master", "", ", ".join(f"{k}={v}" for k, v in s.items() if v))
    if sid in intel: add("intel:" + sid, "supplier_intel", "", ", ".join(f"{k}={v}" for k, v in intel[sid].items() if v))
    for c in clauses:
        if c["contract_id"] == s["contract_id"]:
            extra = ", ".join(f"{k}={c[k]}" for k in ("series", "material_share", "deadband_pct", "days") if c[k])
            add(c["id"], "contract_clause:" + c["type"], "", c["text"] + (f" [{extra}]" if extra else ""))
    for d in docs:
        if d["supplier_id"] == sid:
            meta = ", ".join(f"{k}={d[k]}" for k in ("asked_pct", "asked_price", "effective_date", "notice_days", "part_ids") if d[k])
            add(d["id"], d["kind"], d["date"], body(d["file"]) + (f"\n[metadata: {meta}]" if meta else ""))
    for n in news:
        if n["supplier_id"] == sid: add(n["id"], "news:" + n["topic"], n["date"], f"{n['headline']}. {n['body']}")
    for q in quotes:
        if q["supplier_id"] == sid:
            add(q["id"], "quote", q["quote_date"], f"Quote for {q['part_id']}: {q['unit_price']} {q['currency']}/{q['price_unit']}, lead time {q['lead_time_days']} d, payment {q['payment_days']} d, tooling {q['tooling_eur']}, awarded={q['awarded']}. Remarks: {q['remarks'] or '-'}")
    for h in hist:
        if h["supplier_id"] == sid and h["note"]:
            add(h["event_id"], "negotiation_history", h["date"], f"{h['type']} {h['part_id']}: asked {h['asked_pct']} settled {h['settled_pct']}. Note: {h['note']}")
    my_parts = sorted({r["part_id"] for r in sourcing if r["supplier_id"] == sid})
    for p in my_parts:
        st = [f"{r['supplier_id']} {r['status']} quota {r['quota_pct']} tooling {r['tooling_owner']}" for r in sourcing if r["part_id"] == p]
        dr = [f"{r['series_id']} ({series.get(r['series_id'],'')}) {r['weight']}" for r in drivers if r["part_id"] == p]
        add("drivers:" + p, "part_context", "", f"{p} {parts.get(p,{}).get('name','')}. Sourcing: {'; '.join(st)}. Cost drivers (category team): {'; '.join(dr)}")
    if REL:
        for d in rel_docs:
            if d["supplier_id"] == sid:
                units.append({"id": d["id"], "kind": d["kind"], "date": d["date"], "release": "sunday",
                              "text": (REL / d["file"]).read_text(encoding="utf-8").strip()})
        for n in rel_news:
            if n["supplier_id"] == sid:
                units.append({"id": n["id"], "kind": "news:" + n["topic"], "date": n["date"], "release": "sunday",
                              "text": f"{n['headline']}. {n['body']}".strip()})
    out[sid] = {"name": s["name"], "units": units}
json.dump(out, open(Path(__file__).parent / "bundles.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(len(out), sum(len(v["units"]) for v in out.values()), max(len(json.dumps(v)) for v in out.values()))
touched = sorted(k for k, v in out.items() if any(u["release"] == "sunday" for u in v["units"]))
print(f"release: {REL or 'none'}; suppliers with release units ({len(touched)}): {' '.join(touched)}")
