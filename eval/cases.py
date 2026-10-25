"""Held-out cases: the structure of the practice and sandbox cases, varied with a seed.

Every case uses real parts and suppliers from the data package, so an agent's dossier finds real facts
(scorecards, contracts, should-cost, indices). Prices, volumes, alternatives, must-haves and the
negotiable terms are drawn per case. Hidden supplier parameters stay in `hidden`, not in the case.
"""

import json
import random
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

TYPES = (
    ["price_increase"] * 5
    + ["renewal"] * 4
    + ["rfq"] * 3
    + ["raw_material"] * 3
    + ["quality"] * 2
    + ["capacity"] * 2
    + ["bundling"]
)
MATERIAL = (
    "AS-",
    "WB-CU",
    "WB-NI",
    "WB-AL",
    "CU-NOTATION",
    "PPI-C241",
    "PPI-C2451",
    "PPI-C2452",
    "PPI-C2431",
)
PAY = [14, 30, 45, 60, 90]
GOALS = ["leverage", "leverage", "routine", "stabilise", "secure_supply"]


@dataclass
class World:
    """What the supplier side knows: the data package's facts and a few tables."""

    root: Path
    facts: dict = field(default_factory=dict)
    drivers: dict = field(
        default_factory=dict
    )  # part -> [(series, weight)], heaviest first
    should_cost: dict = field(default_factory=dict)  # part -> fact id
    suppliers: dict = field(default_factory=dict)
    intel: dict = field(default_factory=dict)
    parts: dict = field(default_factory=dict)

    @classmethod
    def load(cls, root):
        root = Path(root)
        w = cls(root, json.loads((root / "facts.json").read_text(encoding="utf-8")))
        db = sqlite3.connect(f"file:{next(root.rglob('*.sqlite'))}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        for r in db.execute("SELECT * FROM cost_drivers ORDER BY weight DESC"):
            w.drivers.setdefault(r["part_id"], []).append((r["series_id"], r["weight"]))
        for r in db.execute("SELECT id, part_id FROM should_cost"):
            w.should_cost[r["part_id"]] = r["id"]
        w.suppliers = {r["id"]: dict(r) for r in db.execute("SELECT * FROM suppliers")}
        w.intel = {
            r["supplier_id"]: dict(r)
            for r in db.execute("SELECT * FROM supplier_intel")
        }
        w.parts = {r["id"]: dict(r) for r in db.execute("SELECT * FROM parts")}
        w.db = db
        return w

    def name(self, sup):
        return (self.suppliers.get(sup) or {}).get("name") or sup

    def index_change(self, series, months=12, until="2026-08"):
        """Relative change of a series over `months` up to `until`, from the facts."""
        y, m = map(int, until.split("-"))
        m0 = (y * 12 + m - 1) - months
        start = f"{m0 // 12}-{m0 % 12 + 1:02d}"
        a, b = (
            self.facts.get(f"idx:{series}@{start}"),
            self.facts.get(f"idx:{series}@{until}"),
        )
        if not a or not b or not a.get("value"):
            return None
        return b["value"] / a["value"] - 1


def _round(x, step=10):
    return int(round(x / step) * step)


def _snap(options, value):
    return min(options, key=lambda o: (abs(o - value), o))


def _months_after(d, months):
    m = d.month - 1 + months
    return date(d.year + m // 12, m % 12 + 1, 1)


def make_case(world, n, kind, rng):
    db = world.db
    rows = db.execute(
        """SELECT s.part_id, s.supplier_id, p.price, p.valid_from FROM sourcing s
           JOIN price_conditions p ON p.part_id = s.part_id AND p.supplier_id = s.supplier_id
           JOIN parts pa ON pa.id = s.part_id
           WHERE s.status = 'active' AND pa.price_unit = 1
           ORDER BY s.part_id, s.supplier_id, p.valid_from"""
    ).fetchall()
    latest = {}
    for r in rows:
        latest[(r["part_id"], r["supplier_id"])] = r["price"]
    pairs = sorted(latest)
    if kind == "rfq":
        multi = {}
        for r in db.execute("SELECT part_id, supplier_id FROM sourcing"):
            multi.setdefault(r["part_id"], set()).add(r["supplier_id"])
        pairs = [p for p in pairs if len(multi.get(p[0], ())) >= 2]
    if kind == "raw_material":
        pairs = [
            p
            for p in pairs
            if any(s.startswith(MATERIAL) for s, _ in world.drivers.get(p[0], []))
        ]
    part, sup = rng.choice(pairs)
    current = round(latest[(part, sup)], 2)
    suppliers = [sup]
    if kind == "rfq":
        others = sorted(
            {
                r[0]
                for r in db.execute(
                    "SELECT supplier_id FROM sourcing WHERE part_id = ?", (part,)
                )
            }
            - {sup}
        )
        suppliers += others[: rng.choice([1, 2])]
        if len(suppliers) < 3 and rng.random() < 0.5:
            same_cat = sorted(
                s for s, row in world.suppliers.items()
                if row.get("category") == world.suppliers[sup].get("category") and s not in suppliers
                and s in world.intel
            )  # fmt: skip
            if same_cat:
                suppliers.append(rng.choice(same_cat))
    info = world.parts[part]
    fc = db.execute(
        "SELECT qty_next_12m FROM forecasts WHERE part_id = ? ORDER BY snapshot_month DESC LIMIT 1",
        (part,),
    ).fetchone()
    need = _round(
        (fc[0] if fc and fc[0] else rng.randint(2000, 40000)) * rng.uniform(0.85, 1.15)
    )
    need = max(need, 500)
    horizon = rng.choice([1, 2, 2, 3]) if kind not in ("capacity",) else 1
    need_lt = int(info.get("planned_lead_time_days") or 42)
    start = date(2027, 1, 1)
    freight_exw = round(current * rng.uniform(0.01, 0.04), 2)
    buyer = {
        "need_per_year": need,
        "horizon_years": horizon,
        "current_unit_price": None if kind == "rfq" and rng.random() < 0.3 else current,
        "fallback_unit_price": round(current * rng.uniform(1.08, 1.20), 2),
        "interest_rate": 0.08,
        "reference_payment_days": 60,
        "need_lead_time_days": need_lt,
        "category_goal": rng.choice(GOALS),
        "fallback_risk_pct": rng.choice([3, 5]),
        "index_risk_pct": rng.choice([1, 2]),
        "late_cost_per_day": rng.choice([50, 96, 200, 300]),
        "cost_per_defect": rng.choice([0, 25, 50, 80]),
        "freight_eur_per_unit": {
            "EXW": freight_exw,
            "FCA": round(freight_exw * 0.66, 2),
            "DAP": 0.0,
        },
        "index_paths": {},
        "must_haves": [],
        "alternative": {"kind": "none", "ref": None, "terms": {}},
    }
    # The arena sends these two on relationship goals (brief/api.md); derived, not drawn, so the
    # seeded case set stays identical.
    if buyer["category_goal"] in ("stabilise", "secure_supply") and current:
        buyer["sustainable_margin_pct"] = 8.0
        buyer["relationship_eur_per_point"] = round(0.018 * need * current, -1)
    contract_opts = [12, 24, 36][: max(1, horizon)] if horizon > 1 else [12, 24]
    lt_opts = sorted({max(7, need_lt + d) for d in (-14, -7, 0, 7, 14)})
    vol_opts = sorted({_round(need * f) for f in (0.8, 0.9, 1.0, 1.1)})
    drivers = [s for s, _ in world.drivers.get(part, [])]
    clause_series = next(
        (s for s in drivers if s.startswith(MATERIAL)),
        drivers[0] if drivers else "PPI-C241",
    )
    share = round(rng.choice([0.3, 0.4, 0.45, 0.5]), 2)
    clauses = [None] + [
        {
            "series": clause_series,
            "material_share": share,
            "deadband_pct": d,
            "base_month": "2026-08",
        }
        for d in (0, 3, 5)
    ]
    negotiable = {
        "price_increase": ["unit_price", "payment_days", "effective_date"],
        "renewal": ["unit_price", "payment_days", "contract_months"],
        "rfq": ["unit_price", "payment_days", "lead_time_days"],
        "raw_material": [
            "unit_price",
            "index_clause",
            "payment_days",
            "contract_months",
        ],
        "quality": ["unit_price", "ppm_commitment", "chargeback_eur_per_complaint"],
        "capacity": ["unit_price", "lead_time_days", "volume_per_year", "payment_days"],
        "bundling": ["unit_price", "payment_days", "contract_months"],
    }[kind]
    if kind == "renewal" and rng.random() < 0.4:
        negotiable.append("index_clause")
    if kind == "rfq" and rng.random() < 0.4:
        negotiable.append("incoterm")
    if kind == "quality" and rng.random() < 0.5:
        negotiable.append("lead_time_days")
    if kind == "price_increase" and rng.random() < 0.3:
        negotiable.append("volume_per_year")
    options = {
        "payment_days": PAY,
        "contract_months": contract_opts,
        "effective_date": [start.isoformat(), _months_after(start, 3).isoformat()],
        "lead_time_days": lt_opts,
        "volume_per_year": vol_opts,
        "ppm_commitment": [3000, 2000, 1500, 1000, 500],
        "chargeback_eur_per_complaint": [0, 150, 300, 500],
        "incoterm": ["EXW", "FCA", "DAP"],
        "index_clause": clauses,
    }
    term_options = {f: options[f] for f in negotiable[1:]}
    fixed = {
        "lead_time_days": need_lt,
        "volume_per_year": need,
        "contract_months": 12 * horizon,
        "effective_date": start.isoformat(),
        "index_clause": None,
        "incoterm": world.suppliers.get(sup, {}).get("incoterm") or "FCA",
        "tooling_eur": 0,
    }
    if fixed["incoterm"] not in buyer["freight_eur_per_unit"]:
        fixed["incoterm"] = "FCA"
    fixed = {k: v for k, v in fixed.items() if k not in negotiable}
    if "index_clause" in negotiable:
        buyer["index_paths"][clause_series] = round(rng.uniform(-0.02, 0.05), 3)
    if kind == "rfq":
        buyer["must_haves"].append(
            {"field": "lead_time_days", "op": "<=", "value": need_lt}
        )
    if kind == "capacity":
        buyer["must_haves"].append(
            {"field": "volume_per_year", "op": ">=", "value": _round(need * 0.95)}
        )
    if kind == "quality":
        buyer["must_haves"].append(
            {"field": "ppm_commitment", "op": "<=", "value": rng.choice([1500, 2000])}
        )

    hidden = {
        "ask_pct": round(rng.uniform(0.05, 0.14), 3),
        "rfq_spread": {s: round(rng.uniform(0.95, 1.25), 3) for s in suppliers},
        "room": round(rng.uniform(0.07, 0.16), 3),
        "trap": False,
        "base_price": current,
    }
    facts, refs = {}, []
    alt_kind = rng.random()
    if alt_kind < 0.75 or kind == "renewal":
        trap = rng.random() < 0.12
        hidden["trap"] = trap
        factor = rng.uniform(0.84, 0.9) if trap else rng.uniform(1.03, 1.16)
        rival = next((s for s in sorted(world.suppliers) if s not in suppliers and world.suppliers[s].get("category")
                      == world.suppliers[sup].get("category")), "sup:79999")  # fmt: skip
        qid = f"quote:79{n:03d}{rng.randint(10, 99)}"
        alt_terms = {
            "unit_price": round(current * factor, 2),
            "payment_days": rng.choice([30, 45, 60]),
            "lead_time_days": need_lt + rng.choice([0, 7]),
            "volume_per_year": need,
            "contract_months": 12 * horizon,
            "effective_date": start.isoformat(),
            "index_clause": None,
            "incoterm": "DAP",
            "tooling_eur": 0,
        }
        buyer["alternative"] = {"kind": "quote", "ref": qid, "terms": alt_terms}
        facts[qid] = {
            "date": "2026-09-15",
            "incoterm": "DAP",
            "lead_time_days": alt_terms["lead_time_days"],
            "part": part,
            "payment_days": alt_terms["payment_days"],
            "price_unit": 1,
            "supplier": rival,
            "unit_price": alt_terms["unit_price"],
        }
        refs.append(qid)
    for s in suppliers:
        sc = f"sc:{s[4:]}@2026Q3"
        if sc in world.facts:
            refs.append(sc)
        ctr = (world.suppliers.get(s) or {}).get("contract_id")
        if ctr and f"ctr:{ctr}" in world.facts:
            refs.append(f"ctr:{ctr}")
    for series in drivers[:2]:
        for m in ("2025-08", "2026-08"):
            if f"idx:{series}@{m}" in world.facts:
                refs.append(f"idx:{series}@{m}")
    for r in refs:
        if r in world.facts:
            facts[r] = world.facts[r]
    names = ", ".join(world.name(s) for s in suppliers)
    title = {
        "price_increase": f"{world.name(sup)} asks +{round(hidden['ask_pct'] * 100)} % on {info['description']}",
        "renewal": f"{info['description']}: agreement with {world.name(sup)} expires",
        "rfq": f"RFQ: {info['description']}, {len(suppliers)} bidders",
        "raw_material": f"{info['description']}: material surcharge from {world.name(sup)}",
        "quality": f"{world.name(sup)}: quality on {info['description']}",
        "capacity": f"{world.name(sup)} is full: {info['description']}",
        "bundling": f"Bundle {info['description']} with {world.name(sup)}",
    }[kind]
    alt = buyer["alternative"]
    musts = (
        "; ".join(f"{m['field']} {m['op']} {m['value']}" for m in buyer["must_haves"])
        or "none"
    )
    briefing = (
        f"Held-out bank case. You negotiate for Hollerau Pumpenwerke with {names}. {title}. "
        f"Need: {need} pc a year for {horizon} year(s). Category goal: {buyer['category_goal']}. "
        f"Fallback if you walk away: {alt['ref'] or 'none'}. Must-haves: {musts}."
    )
    case = {
        "case_id": f"case:E{n:02d}",
        "set": "practice",
        "type": kind,
        "title": title,
        "briefing": briefing,
        "trigger_doc": None,
        "parts": [part],
        "suppliers": suppliers,
        "negotiable": negotiable,
        "fixed_terms": fixed,
        "term_options": term_options,
        "buyer": buyer,
        "round_limit": 10,
        "refs": refs,
        "facts": facts,
    }
    return case, hidden


def generate(world, count=20, seed=2026):
    """`count` cases, deterministic for a seed: {case_id: (case, hidden)}."""
    out = {}
    for n in range(1, count + 1):
        rng = random.Random(f"case:{seed}:{n}")
        kind = TYPES[(n - 1) % len(TYPES)] if count <= len(TYPES) else rng.choice(TYPES)
        case, hidden = make_case(world, n, kind, rng)
        out[case["case_id"]] = (case, hidden)
    return out
