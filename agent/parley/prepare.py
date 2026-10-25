"""Before the negotiations: the plan model reads each detailed supplier's notes, letters and news
once and writes a short profile. Cached in cache/personas.json. Code keeps only flags it can ground
in the source text; the summary is context for the writer, never a number for the policy."""

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor

from .dossier import MATERIAL_SERIES_WIDE, ROOT, Dossier, signal

log = logging.getLogger("parley")
CACHE = ROOT / "cache" / "personas.json"

PLAN_SYSTEM = """You prepare a buyer for a negotiation with one industrial supplier. You get the
supplier's register data, our call and meeting notes, e-mails, letters and press items.
Reply with one JSON object only:
{"summary": "3-4 sentences: who sits at the desk, what the company is dealing with now, what it
  wants from us, how to approach it",
 "patience": "low|medium|high",
 "cash_short": true|false,
 "cash_evidence": "a short verbatim quote from the material, or empty",
 "hedged_series": ["index series IDs the supplier has covered at fixed prices, as written in the material"],
 "hedge_evidence": "a short verbatim quote, or empty",
 "levers": ["terms or gestures that would move it, e.g. faster payment, binding volume, longer contract"],
 "avoid": ["what would irritate it"]}
Only state what the material supports."""


def material(d: Dossier, sid):
    p = d.profiles[sid]
    rows = d._q("SELECT * FROM supplier_intel WHERE supplier_id = ?", (sid,))
    news = d.news(sid)
    hist = d._q(
        "SELECT date, type, asked_pct, settled_pct, note FROM negotiation_history WHERE supplier_id = ? "
        "ORDER BY date DESC LIMIT 8",
        (sid,),
    )
    out = [f"Supplier {sid}: {p.name}", f"Register: {json.dumps(rows[0] if rows else {}, ensure_ascii=False)}"]
    out += [f"Press {n['date']} ({n['topic']}): {n['headline']}. {n['body']}" for n in news[:6]]
    out += [
        f"Past negotiation {h['date']} {h['type']}: asked {h['asked_pct']} settled {h['settled_pct']}. {h['note'] or ''}"
        for h in hist
    ]
    out += [f"--- {n['kind']} {n['date']} ({n['id']})\n{n['text']}" for n in p.notes[:10]]
    return "\n".join(out)[:14000]


def grounded(persona, text, series):
    out = {k: persona.get(k) for k in ("summary", "patience", "levers", "avoid")}
    ev = persona.get("cash_evidence") or ""
    out["cash_short"] = bool(persona.get("cash_short")) and len(ev) > 8 and ev[:40] in text
    hedge_ev = persona.get("hedge_evidence") or ""
    named = set()
    if signal("hedge") and len(hedge_ev) > 8 and hedge_ev[:40] in text:
        for word, ids in MATERIAL_SERIES_WIDE.items():
            if re.search(rf"\b{word}", hedge_ev, re.I):
                named.update(ids)
    out["hedged_series"] = [
        s
        for s in persona.get("hedged_series") or []
        if s in series and (s in text or s in named) and (not hedge_ev or hedge_ev[:40] in text)
    ]
    return out


def data_version(package):
    """Which data the profiles were built from: the package, plus the Sunday release if present."""
    release = getattr(package, "release", None)
    if not release:
        return "package-v1"
    stat = (release / "facts.json").stat()
    return f"package-v1+release-{stat.st_size}"


def release_suppliers(package):
    """Suppliers with their own news or letters in the Sunday release."""
    if not getattr(package, "release", None):
        return set()
    return {r["supplier_id"] for r in package._ids.values() if r.get("root") and r.get("supplier_id")}


def prepare(package, llm, force=False):
    if package is None:
        raise SystemExit("no data package: run `parley data` first")
    version = data_version(package)
    cache = {}
    if CACHE.is_file() and not force:
        cache = json.loads(CACHE.read_text(encoding="utf-8"))
        if cache.get("_meta", {}).get("data") != version:
            print(f"data changed ({cache.get('_meta', {}).get('data')} -> {version}): profiles are rebuilt")
            cache = {}
    cache["_meta"] = {"data": version}
    ids = [r["id"] for r in package.query("SELECT id FROM suppliers WHERE detail = 'full' ORDER BY id")]
    todo = [s for s in ids if s not in cache]
    if not todo:
        print(f"{len(cache) - 1} profiles cached in {CACHE} for data {version}")
        return cache
    if llm is None:
        raise SystemExit("prepare needs the plan model (ANTHROPIC_API_KEY)")
    fake_case = {"case_id": "prepare", "suppliers": todo, "parts": [], "refs": [], "buyer": {}}
    d = Dossier(fake_case, package)
    series = set(d.series)

    def one(sid):
        text = material(d, sid)
        out = llm.json(PLAN_SYSTEM, text, role="plan", timeout=90, max_tokens=4000, effort="medium")
        if not isinstance(out, dict):
            log.warning("%s: no profile", sid)
            return sid, None
        return sid, grounded(out, text, series)

    with ThreadPoolExecutor(max_workers=8) as pool:
        for sid, persona in pool.map(one, todo):
            if persona:
                cache[sid] = persona
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(cache) - 1} profiles in {CACHE} (data {version}); llm {json.dumps(llm.usage.summary())}")
    return cache
