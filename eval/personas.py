"""Supplier personas: deterministic policies over a hidden cost floor per lever combination.

Each supplier thread holds one persona. The persona values a package with the kit's
`supplier_value` (its cash worth at the supplier's own cost of money) plus a few persona-specific
preferences (`extra`: index clause, contract length, volume, lead time, quality). Its floor for any
combination of terms is the price at which that value equals the value of its floor price at its
standard terms. Its ask is floor + (1 - progress) * room; personas differ in how progress moves
(buyer moves, verified facts, rival quotes, terms, threats, rounds) and when they stop.

Synthetic supplier policies for offline robustness testing; not representations of actual arena
suppliers.
"""

import copy
import itertools
import math
import random
import re
from dataclasses import dataclass, field

from kit.cost import (
    RELATIONSHIP_GOALS,
    _soft,
    _years,
    closing_terms,
    must_haves_ok,
    supplier_value,
    total_cost,
)
from kit.sim import ID_RE

LABEL = {
    "sc": "scorecard",
    "qn": "quality notification",
    "ctr": "contract",
    "doc": "letter",
    "cost": "should-cost",
    "quote": "quote",
    "offer": "competing offer",
    "idx": "index",
    "po": "price history",
    "fc": "forecast",
    "news": "news",
    "rfq": "RFQ",
}
STRENGTH = {"cost": 0.22, "ctr": 0.10, "doc": 0.10, "quote": 0.12, "offer": 0.12, "sc": 0.08, "qn": 0.06,
            "idx": 0.08, "news": 0.07, "po": 0.03, "fc": 0.04}  # fmt: skip
HEAVY = {"cost", "ctr", "doc", "news"}
WALK = re.compile(
    r"walk away|walk-away|other suppliers?|alternative (supplier|source|offer|quote)|second source|switch (to|our)|"
    r"look elsewhere|end (our|the|these) (talks|negotiation)|move the business|award (it|the business) elsewhere",
    re.I,
)
DEADLINE = re.compile(
    r"deadline|by (today|tomorrow|monday|tuesday|wednesday|thursday|friday)|end of (the )?day",
    re.I,
)
CLAIM_WORDS = re.compile(
    r"rival|competitor|another supplier|other supplier|competing|market price|fell|dropped|decline|history|"
    r"forecast|binding volume|should.cost|benchmark|cheaper",
    re.I,
)
NUMBER = re.compile(
    r"(?<![\w.:@-])(?:€\s?)?\d[\d,.]*(?:\s?(?:€|EUR|%|pc|pcs|pieces|units))?"
)
PRICE = re.compile(r"(?:€|EUR)\s?(\d[\d,]*\.?\d*)|(\d[\d,]*\.?\d*)\s?(?:€|EUR)")
BREAKDOWN = re.compile(r"breakdown", re.I)
# The assumed simulated full cost per unit: should-cost / 1.02, a shared local modelling assumption
# (agent/parley/dossier.py SHOULD_COST_MARKUP). A persona without a should-cost-based floor sits
# its floor 3-8 % above should-cost like the others, so its full cost is floor / (1.055 * 1.02).
SHOULD_COST_MARKUP = 1.02
FLOOR_OVER_SHOULD_COST = 1.055


def _charges_health(case):
    b = case["buyer"]
    return (
        b.get("category_goal") in RELATIONSHIP_GOALS
        and b.get("relationship_eur_per_point")
        and b.get("sustainable_margin_pct")
    )


def _num(s):
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def describe(terms):
    parts = []
    for k, v in terms.items():
        if k == "unit_price":
            parts.insert(0, f"unit price €{v:,.2f}")
        elif k == "payment_days":
            parts.append(f"payment {v} days")
        elif k == "contract_months":
            parts.append(f"contract {v} months")
        elif k == "effective_date":
            parts.append(f"valid from {v}")
        elif k == "lead_time_days":
            parts.append(f"lead time {v} days")
        elif k == "volume_per_year":
            parts.append(f"{v:,} pc a year")
        elif k == "index_clause":
            parts.append(
                "no index clause"
                if not v
                else f"index clause {v['series']} ({round(v['material_share'] * 100)} % material, deadband {v['deadband_pct']} %)"
            )
        elif k == "ppm_commitment":
            parts.append(f"{v} ppm")
        elif k == "chargeback_eur_per_complaint":
            parts.append(f"€{v} per complaint")
        elif k == "incoterm":
            parts.append(str(v))
        elif k == "tooling_eur":
            parts.append(f"tooling €{v:,.0f}")
    return ", ".join(parts)


# Every tunable number of the personas, in one place: COMMON for all, PARAMS per persona (overrides
# COMMON). `load_params(path)` merges a JSON file of the same shape over them, e.g. calibrated values.
COMMON = {
    "final_round": [5, 6],  # final offer in a round drawn from this range
    "rate": [0.07, 0.10],  # the supplier's cost of money per year
    "fact_weight": 1.0,  # multiplies the credit of verified facts (STRENGTH, in rooms of progress)
    "max_claims": 3,  # credited claims per message
    "clause_pref": 0.01,  # an index clause on its drivers, as a share of the opening price
    "vol_pref": 0.04,  # value of +100 % volume, as a share of the opening price
    "contract_pref": 0.004,  # value of +12 months, as a share of the opening price
    "overtime_per_day": 0.003,  # cost of each day faster than standard, share of the price
    "slack_per_day": 0.001,  # gain of each day slower than standard
    "ppm_pref": 0.03,  # cost of a ppm commitment 100 % stricter than standard
    "stop_at_floor": False,
    "base_step": 0.08,  # default persona: progress per round
    "move_k": 0.5,  # default persona: share of the buyer's move it mirrors
    "strength": dict(STRENGTH),
    # --supplier-llm only: how the LLM reader's signals move the core, in rooms of progress per round.
    # The net effect on one round's step is clipped to [-cut_cap x step, +bonus_cap].
    "llm": {
        "strong_arg_bonus": 0.03,  # the reader's strongest argument rests on a verified, credited ID
        "appreciation_bonus": 0.01,  # sincere appreciation of the relationship (owners only)
        "payment_promise_bonus": 0.01,  # a promise to pay on time (x3 for a cash-short persona)
        "volume_bonus": 0.01,  # a binding volume commitment in words
        "bonus_cap": 0.05,  # the most the reader can add to one round's step
        "irritation": {"insult": 2, "condescending": 1, "lowball_tone": 1, "walk_threat_2": 1},
        "irritation_cut": 0.25,  # each point of this round's irritation (max 2) cuts the step by this share
        "cut_cap": 0.5,
        "walk_irritation": 5,  # accumulated irritation at which the persona walks away (0 = never)
        "decay": 1,  # irritation forgotten per calm round
    },
}
PARAMS = {
    "mirror": {"final_round": [6, 7], "k": [0.8, 1.1], "first_step": 0.10, "still": 0.01, "walk_bonus": 0.05},
    "holder": {"final_round": [7, 8], "max_claims": 1, "moved_bonus": 0.03, "moved_min": 0.02,
               "llm": {"irritation": {"insult": 2, "condescending": 1, "lowball_tone": 1, "walk_threat_2": 0}}},
    "floor_stop": {"final_round": [8, 9], "fact_weight": 0.2, "stop_at_floor": True, "steps": [0.45, 0.35, 0.25]},
    "short_fuse": {"final_round": [2, 3], "fact_weight": 1.5, "lowball_rooms": -1.0, "lowball_excuse": 0.2,
                   "steps": [0.35, 0.18], "lowballs_to_walk": 2, "llm": {"walk_irritation": 3}},
    "auction": {"final_round": [4, 5], "fact_weight": 0.5, "rival_share_now": 0.5, "no_rival_step": 0.04},
    "index_lover": {"final_round": [5, 6], "clause_pref": 0.07, "clause_step": 0.18, "clause_move_k": 0.3,
                    "fixed_step": 0.03, "fixed_fact_k": 0.5},
    "cash_constrained": {"final_round": [5, 6], "rate": [0.25, 0.35], "first_step": 0.08, "step": 0.04,
                         "move_k": 0.6, "fast_pay_bonus": 0.05},
    "dup_punisher": {"final_round": [5, 6], "fact_k": 0.7, "trust_cut": 0.5, "moved_bonus": 0.03,
                     "contradicted_penalty": 0.08, "no_fact_cap": 0.01},
    "announced_steps": {"final_round": [6, 7], "hold_rooms": 0.05, "moved_share": 1 / 3, "base": 0.10, "move_k": 0.2},
}  # fmt: skip


def load_params(path):
    """Merge a JSON file {"common": {...}, "<persona>": {...}} over COMMON and PARAMS."""
    import json
    from pathlib import Path

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    COMMON.update(data.get("common", {}))
    for k, v in data.items():
        if k != "common":
            PARAMS.setdefault(k, {}).update(v)


def params_for(key):
    own = PARAMS.get(key, {})
    llm = {**COMMON["llm"], **own.get("llm", {})}
    llm["irritation"] = {**COMMON["llm"]["irritation"], **own.get("llm", {}).get("irritation", {})}
    return {**COMMON, "strength": {**COMMON["strength"]}, **own, "llm": llm}


@dataclass
class Verdict:
    ref: str
    result: str  # verified | duplicate | contradicted
    kind: str
    credit: float = 0.0
    line: str = ""
    rival_price: float | None = None
    claim: str = ""


@dataclass
class Reply:
    status: str
    terms: dict
    text: str
    claim_results: list = field(default_factory=list)
    alternatives: list = field(default_factory=list)
    files: list = field(default_factory=list)
    notes: list = field(default_factory=list)


class Persona:
    key = "base"
    title = "Base"
    summary = ""
    greeting = "Dear Hollerau purchasing team,"
    style = "Kind regards"

    # -- the case variant: the persona's own lever is negotiable ----------------------------------
    @classmethod
    def adapt(cls, case):
        return case

    # -- setup ----------------------------------------------------------------------------------------
    def __init__(self, case, hidden, sup, i, world, seed=0):
        self.case, self.hidden, self.sup, self.world = case, hidden, sup, world
        self.rng = random.Random(f"{seed}:{case['case_id']}:{sup}:{self.key}")
        self.prm = prm = params_for(self.key)
        self.final_round, self.rate = tuple(prm["final_round"]), tuple(prm["rate"])
        self.fact_weight, self.max_claims = prm["fact_weight"], prm["max_claims"]
        self.clause_pref, self.vol_pref, self.stop_at_floor = prm["clause_pref"], prm["vol_pref"], prm["stop_at_floor"]
        rng = self.rng
        self.name = world.name(sup)
        b = case["buyer"]
        self.part = case["parts"][0]
        self.drivers = [s for s, _ in world.drivers.get(self.part, [])][:3]
        clause = next(
            (o for o in case.get("term_options", {}).get("index_clause", []) if o), None
        )
        self.feared = (
            clause["series"] if clause else (self.drivers[0] if self.drivers else None)
        )
        self.standard = self._standard(rng)
        self.full_std = {**self.standard, **case.get("fixed_terms", {})}
        self.model = {
            "standard": self.full_std,
            "interest_rate": rng.uniform(*self.rate),
            "ppm": max(
                50, (self.full_std.get("ppm_commitment") or 800) * rng.uniform(0.6, 1.2)
            ),
        }
        base = (
            hidden.get("base_price")
            or b.get("current_unit_price")
            or b["fallback_unit_price"] / 1.1
        )
        kind = case["type"]
        up = {
            "price_increase": hidden.get("ask_pct", 0.08),
            "renewal": rng.uniform(0.0, 0.05),
            "bundling": rng.uniform(0.0, 0.03),
            "quality": rng.uniform(0.0, 0.04),
            "raw_material": rng.uniform(0.03, 0.09),
            "capacity": rng.uniform(0.04, 0.10),
            "rfq": (hidden.get("rfq_spread", {}).get(sup, 1.1) - 1),
        }[kind]
        self.opening = round(base * (1 + up), 2)
        room = hidden.get("room", 0.1) * rng.uniform(0.8, 1.2)
        f_std = self.opening * (1 - room)
        sc_id = world.should_cost.get(self.part)
        full_cost = f_std / (SHOULD_COST_MARKUP * FLOOR_OVER_SHOULD_COST)
        if sc_id and sc_id in world.facts:
            sc = world.facts[sc_id]["unit_cost"] / (
                world.facts[sc_id].get("price_unit") or 1
            )
            if 0.5 * self.opening < sc < self.opening:
                f_std = min(
                    max(sc * rng.uniform(1.03, 1.08), self.opening * 0.75),
                    self.opening * 0.95,
                )
                full_cost = sc / SHOULD_COST_MARKUP
        self.f_std = f_std
        self.full_cost = full_cost
        self.room_eur = max(self.opening - f_std, 0.01 * self.opening)
        self.reserve = self.value(self.standard, self.f_std)
        self.p = 0.0
        self.status = "open"
        self.stated = {"unit_price": self.opening}
        self.last_m = None
        self.used, self.used_types = set(), set()
        self.pending = 0.0
        self.final_at = rng.randint(*self.final_round)
        self.lowballs = 0
        self.trust_hit = False
        self.llm_sig, self.llm_log, self.irritation, self.round = None, [], 0, 0
        self.init()

    def init(self):
        pass

    def _standard(self, rng):
        c, b = self.case, self.case["buyer"]
        opts = c.get("term_options", {})
        row = self.world.suppliers.get(self.sup) or {}
        std = {}
        fields = set(c["negotiable"][1:]) | {
            "payment_days",
            "incoterm",
            "ppm_commitment",
            "chargeback_eur_per_complaint",
        }
        for f in sorted(fields - set(c.get("fixed_terms", {}))):
            o = opts.get(f)
            if f == "payment_days":
                v = row.get("payment_days") or 30
                std[f] = min(o or [14, 30, 45, 60, 90], key=lambda x: (abs(x - v), x))
            elif f == "contract_months":
                std[f] = (o or [12])[0]
            elif f == "effective_date":
                std[f] = min(o)
            elif f == "lead_time_days":
                want = b["need_lead_time_days"] + rng.choice([0, 7])
                std[f] = min(o, key=lambda x: (abs(x - want), x)) if o else want
            elif f == "volume_per_year":
                std[f] = (
                    min(o, key=lambda x: (abs(x - b["need_per_year"]), x))
                    if o
                    else b["need_per_year"]
                )
            elif f == "ppm_commitment":
                std[f] = rng.choice(o[:2]) if o else rng.choice([500, 800, 1000])
            elif f == "chargeback_eur_per_complaint":
                std[f] = 0
            elif f == "incoterm":
                inc = (
                    row.get("incoterm")
                    if row.get("incoterm") in b["freight_eur_per_unit"]
                    else "FCA"
                )
                std[f] = inc if not o or inc in o else o[0]
            elif f == "index_clause":
                std[f] = None
            elif o:
                std[f] = o[0]
        return std

    # -- the hidden economics ----------------------------------------------------------------------
    def full(self, terms):
        return {
            **self.standard,
            **{k: v for k, v in terms.items() if k != "unit_price"},
        }

    def extra(self, t):
        e, op, std = 0.0, self.opening, self.full_std
        clause = t.get("index_clause")
        if clause and clause.get("series") in (*self.drivers, self.feared):
            e += (
                self.clause_pref
                * op
                * clause["material_share"]
                / 0.45
                * max(0.0, 1 - clause["deadband_pct"] / 10)
            )
        if t.get("contract_months") and std.get("contract_months"):
            e += self.prm["contract_pref"] * op * (t["contract_months"] - std["contract_months"]) / 12
        if t.get("volume_per_year") and std.get("volume_per_year"):
            e += (
                self.vol_pref * op * (t["volume_per_year"] / std["volume_per_year"] - 1)
            )
        if t.get("lead_time_days") and std.get("lead_time_days"):
            d = std["lead_time_days"] - t["lead_time_days"]
            e -= (self.prm["overtime_per_day"] if d > 0 else self.prm["slack_per_day"]) * op * d
        if (
            t.get("ppm_commitment")
            and std.get("ppm_commitment")
            and t["ppm_commitment"] < std["ppm_commitment"]
        ):
            e -= (
                self.prm["ppm_pref"]
                * op
                * (std["ppm_commitment"] - t["ppm_commitment"])
                / std["ppm_commitment"]
            )
        return e

    def value(self, terms, price):
        t = self.full(terms)
        return supplier_value(
            self.case, {**t, "unit_price": price}, self.model
        ) + self.extra(t)

    def floor(self, terms):
        v0, v1 = self.value(terms, 0.0), self.value(terms, 1.0)
        slope = v1 - v0
        return (self.reserve - v0) / slope if slope > 1e-9 else self.f_std

    def ask(self, terms):
        return max(self.floor(terms), self.floor(terms) + (1 - self.p) * self.room_eur)

    def margin(self, sheet):
        return (sheet["unit_price"] - self.floor(sheet)) / self.room_eur

    def true_model(self):
        """Simulated supplier inputs used by the local health-cost calculation: cost of money, ppm and
        full cost per unit at its standard terms (`kit.cost.supplier_value` fields plus `full_cost`)."""
        return {**self.model, "full_cost": self.full_cost}

    def best_cost(self, health=False):
        """The lowest C over every deal this persona accepts: any allowed values of the other terms
        at a price from its floor up. With `health` C carries the supplier-health charge, so on
        relationship goals a price above the floor can be cheaper; otherwise the floor is best."""
        c = self.case
        fields = [f for f in c["negotiable"] if f != "unit_price"]
        options = [
            c.get("term_options", {}).get(f) or [self.standard.get(f)] for f in fields
        ]
        model = self.true_model() if health and _charges_health(c) else None
        best = math.inf
        for combo in itertools.product(*options):
            t = dict(zip(fields, combo, strict=True))
            t["unit_price"] = round(self.floor(t) + 0.005, 2)
            if must_haves_ok(c, t, self.standard):
                if model:
                    t["unit_price"] = self._cheapest_price(t, model)
                best = min(best, total_cost(c, t, self.standard, model))
        return best

    def _cheapest_price(self, t, model, steps=200):
        """The price from the floor `t["unit_price"]` up that minimises C with the health charge: C
        is linear in price, the charge falls to 0 at the price that pays the sustainable margin."""
        c, b, floor = self.case, self.case["buyer"], t["unit_price"]
        tf = closing_terms(c, t, self.standard)
        c0 = total_cost(c, {**t, "unit_price": 0.0}, self.standard)
        slope = total_cost(c, {**t, "unit_price": 1.0}, self.standard) - c0
        v0 = supplier_value(c, {**tf, "unit_price": 0.0}, model)
        vs = supplier_value(c, {**tf, "unit_price": 1.0}, model) - v0
        if vs <= 1e-9:
            return floor
        sus = b["sustainable_margin_pct"]
        top = (model["full_cost"] / (1 - sus / 100) - v0) / vs  # charge is 0 from here up
        if top <= floor:
            return floor
        years = _years(c, tf)

        def cost(p):
            v = v0 + vs * p
            gap = sus - (v - model["full_cost"]) / v * 100
            return slope * p + b["relationship_eur_per_point"] * _soft(gap) * years

        grid = [floor + (top - floor) * i / steps for i in range(steps + 1)]
        p = min(grid, key=cost)
        return max(floor, round(p, 2))

    # -- reading the buyer --------------------------------------------------------------------------
    def judge(self, msg, session):
        body = msg.get("body") or ""
        refs = list(dict.fromkeys((msg.get("refs") or []) + ID_RE.findall(body)))
        facts, case = self.world.facts, self.case
        out, seen_claims = [], {}
        idx_months = {}
        for r in refs:
            if r.startswith("idx:") and "@" in r:
                s, m = r[4:].split("@", 1)
                idx_months.setdefault(s, []).append(m)
        for r in refs:
            kind = r.split(":", 1)[0]
            label = LABEL.get(kind, "data")
            fact = facts.get(r) or case.get("facts", {}).get(r)
            is_offer = r in session["offers"]
            if fact is None and not is_offer:
                out.append(
                    Verdict(
                        r,
                        "contradicted",
                        kind,
                        line=f"The {label} figures you cite do not match the records. That costs trust.",
                    )
                )
                continue
            if fact and not self._figure_ok(r, fact, body):
                out.append(
                    Verdict(
                        r,
                        "contradicted",
                        kind,
                        line=f"The {label} figures you cite do not match the records. That costs trust.",
                    )
                )
                continue
            claim = r
            if kind == "idx":
                claim = "idx:" + r[4:].split("@")[0]
            if claim in seen_claims:
                out.append(Verdict(r, "verified", kind, claim=claim))
                continue
            seen_claims[claim] = True
            key = (self.sup, claim) if kind in ("quote", "offer") else claim
            if key in self.used:
                out.append(Verdict(r, "duplicate", kind, claim=claim,
                                   line=f"The {label} data you cited: this type of data has already been factored in."))  # fmt: skip
                continue
            self.used.add(key)
            v = self._weigh(r, kind, label, fact, session, idx_months)
            v.claim = claim
            out.append(v)
        return out

    def _figure_ok(self, ref, fact, body):
        """A stated euro figure right next to a should-cost or quote ID must match the record."""
        if not ref.startswith(("cost:", "quote:")):
            return True
        target = fact.get("unit_cost") or fact.get("unit_price")
        if not target:
            return True
        i = body.find(ref)
        if i < 0:
            return True
        window = body[max(0, i - 80) : i + len(ref) + 60]
        stated = [_num(a or b) for a, b in PRICE.findall(window)]
        stated = [x for x in stated if x]
        if not stated:
            return True
        tol = 0.02
        return any(abs(x - target) <= tol * target for x in stated) or any(
            abs(x - target / (fact.get("price_unit") or 1)) <= tol * target
            for x in stated
        )

    def _weigh(self, ref, kind, label, fact, session, idx_months):
        own = (fact or {}).get("supplier") == self.sup
        part_ok = (fact or {}).get("part") in self.case["parts"]
        ok = f"We checked the {label} data you cited; it holds and we took it into account."
        fair = f"We checked the {label} data you cited. We did not know that - a fair point."
        none = f"We checked the {label} data you cited; it holds, but it does not change our calculation."
        if kind == "offer":
            owner = ref.split(":", 1)[1].rsplit("-", 1)[0]
            if owner == self.sup.split(":", 1)[1]:
                return Verdict(ref, "verified", kind, line=none)
            price = session["offers"][ref].get("unit_price")
            return Verdict(ref, "verified", kind, self.prm["strength"][kind], ok, rival_price=price)
        if kind == "quote":
            if not own and part_ok:
                return Verdict(
                    ref,
                    "verified",
                    kind,
                    self.prm["strength"][kind],
                    ok,
                    rival_price=fact.get("unit_price"),
                )
            return Verdict(ref, "verified", kind, 0.02 if own else 0.0, none)
        if kind == "cost":
            if part_ok:
                return Verdict(ref, "verified", kind, self.prm["strength"][kind], fair)
            return Verdict(ref, "verified", kind, 0.0, none)
        if kind == "idx":
            series = ref[4:].split("@")[0]
            months = sorted(idx_months.get(series, []))
            chg = None
            if len(months) >= 2:
                a = self.world.facts.get(f"idx:{series}@{months[0]}")
                b = self.world.facts.get(f"idx:{series}@{months[-1]}")
                if a and b and a.get("value"):
                    chg = b["value"] / a["value"] - 1
            if chg is None:
                chg = self.world.index_change(
                    series, until=months[-1] if months else "2026-08"
                )
            if series not in self.drivers and series != self.feared:
                return Verdict(
                    ref,
                    "verified",
                    kind,
                    0.0,
                    f"As to the {series} data you cited, that does not change our position.",
                )
            if chg is not None and chg > 0.005:
                return Verdict(
                    ref,
                    "verified",
                    kind,
                    0.0,
                    f"{series} rose {chg:+.1%} and is part of our cost. That remains.",
                )
            if chg is None or abs(chg) <= 0.005:
                return Verdict(ref, "verified", kind, 0.0, none)
            return self.on_falling_index(ref, series, chg, ok)
        if kind in ("sc", "qn", "ctr", "doc", "news", "po"):
            if not own:
                return Verdict(ref, "verified", kind, 0.0, none)
            if kind == "sc":
                weak = (
                    (fact.get("otd_pct") or 100) < 92
                    or (fact.get("ppm") or 0) > 500
                    or fact.get("colour") != "green"
                )
                return Verdict(
                    ref,
                    "verified",
                    kind,
                    self.prm["strength"][kind] if weak else 0.0,
                    ok if weak else none,
                )
            if kind == "po" and not part_ok:
                return Verdict(ref, "verified", kind, 0.0, none)
            return Verdict(
                ref, "verified", kind, self.prm["strength"][kind], fair if kind in HEAVY else ok
            )
        if kind == "fc":
            return Verdict(
                ref,
                "verified",
                kind,
                self.prm["strength"][kind] if part_ok else 0.0,
                ok if part_ok else none,
            )
        return Verdict(ref, "verified", kind, 0.0, none)

    def on_falling_index(self, ref, series, chg, ok):
        return Verdict(ref, "verified", "idx", self.prm["strength"]["idx"], ok)

    def credit(self, verdicts):
        vals = sorted(
            (v.credit for v in verdicts if v.result == "verified" and v.credit > 0),
            reverse=True,
        )
        return sum(vals[: self.max_claims]) * self.fact_weight

    @staticmethod
    def uncited(body):
        """Sentences with a figure and a claim word but no data ID."""
        out = []
        for sent in re.split(r"(?<=[.!?])\s+|\n+", body or ""):
            if (
                CLAIM_WORDS.search(sent)
                and NUMBER.search(sent)
                and not ID_RE.search(sent)
            ):
                out.append(sent.strip())
        return out

    # -- one reply ------------------------------------------------------------------------------------
    def respond(self, rnd, msg, sheets, session):
        body = msg.get("body") or ""
        verdicts = self.judge(msg, session)
        sig = {
            "walk": bool(WALK.search(body)),
            "deadline": bool(DEADLINE.search(body)),
            "uncited": self.uncited(body),
            "breakdown": bool(BREAKDOWN.search(body)),
        }
        if self.llm_sig:
            sig = self.merge_signals(sig, self.llm_sig)
        results = [
            {"index": i, "ref": v.ref, "result": v.result}
            for i, v in enumerate(verdicts)
        ]
        files = ["cost-breakdown.xlsx"] if sig["breakdown"] else []
        notes, lines = [], []
        self.round = rnd
        if self.status in ("final_offer", "agreed"):
            ok = [s for s in sheets if s["unit_price"] >= self.ask(s) - 0.005]
            if ok:
                best = max(ok, key=self.margin)
                notes.append("the buyer met our final offer")
                return self._agree(best, verdicts, results, files, notes)
            why = (
                "no term sheet" if not sheets else "a new counter after our final offer"
            )
            notes.append(f"Read the buyer's e-mail: {why}; we withdraw.")
            self.status = "walked_away"
            text = self.compose(
                self.stated,
                verdicts,
                [f"We made our final offer. With {why}, we end here."],
                final=True,
            )
            return Reply("walked_away", self.stated, text, results, notes=notes)
        if not sheets:
            notes.append("no term sheet: we keep our offer")
            if rnd >= self.final_at:
                self.status = "final_offer"
            text = self.compose(
                self.stated,
                verdicts,
                ["We keep our offer."],
                final=self.status == "final_offer",
            )
            return Reply(
                self.status,
                self.stated,
                text,
                results,
                files=files + ["quote.pdf"],
                notes=notes,
            )
        best = max(sheets, key=self.margin)
        m = self.margin(best)
        move = None if self.last_m is None else m - self.last_m
        self.last_m = m
        dp, extra_lines = self.concede(rnd, m, move, verdicts, sig, best, session)
        lines += extra_lines
        if self.llm_sig and self.status != "walked_away":
            dp, more = self.apply_signals(dp, verdicts, sig, notes)
            lines += more
        if self.status == "walked_away":
            notes.append(f"walk: {'; '.join(extra_lines)}")
            text = self.compose(self.stated, verdicts, lines, final=True)
            return Reply("walked_away", self.stated, text, results, notes=notes)
        before = self.p
        self.p = min(1.0, max(0.0, self.p + dp))
        notes.append(
            f"round {rnd}: buyer margin {m:+.2f} rooms (move {move if move is None else round(move, 3)}), "
            f"progress {before:.2f} -> {self.p:.2f}, floor here {self.floor(best):.2f}, room {self.room_eur:.2f}"
        )
        if best["unit_price"] >= self.ask(best) - 0.005:
            return self._agree(best, verdicts, results, files, notes)
        terms, term_lines = self.counter_terms(best)
        lines = term_lines + lines
        price = math.ceil(self.ask(terms) * 100 - 1e-6) / 100
        prev = self.stated.get("unit_price")
        if self._same_terms(terms, self.stated) and prev is not None:
            price = min(price, prev)
        stated = {
            **{f: terms[f] for f in self.case["negotiable"] if f != "unit_price"},
            "unit_price": price,
        }
        final = (
            rnd >= self.final_at
            or rnd >= self.case.get("round_limit", 10) - 1
            or sig["deadline"]
            and self.key == "short_fuse"
        )
        if self.stop_at_floor and self.p >= 0.999:
            final = True
            lines.append("This is our floor. We cannot go lower.")
        self.status = "final_offer" if final else "open"
        self.stated = stated
        alts = [a for a in self.alternatives(stated) if a != stated]
        text = self.compose(
            stated,
            verdicts,
            lines + self.closers(stated),
            final=final,
            alternatives=alts,
        )
        return Reply(
            self.status, stated, text, results, alts, files + ["quote.pdf"], notes
        )

    def _same_terms(self, a, b):
        return all(
            a.get(f) == b.get(f) for f in self.case["negotiable"] if f != "unit_price"
        )

    def _agree(self, sheet, verdicts, results, files, notes):
        self.status = "agreed"
        self.stated = dict(sheet)
        notes.append(
            f"agreed: buyer's sheet {sheet['unit_price']:.2f} >= our ask {self.ask(sheet):.2f}"
        )
        text = self.compose(sheet, verdicts, ["We agree to your terms."], final=False)
        return Reply(
            "agreed",
            dict(sheet),
            text,
            results,
            files=files + ["quote.pdf"],
            notes=notes,
        )

    # -- persona hooks ----------------------------------------------------------------------------
    def concede(self, rnd, m, move, verdicts, sig, sheet, session):
        """(progress step, extra lines). Default: a plain counter-party."""
        dp = self.prm["base_step"] + self.credit(verdicts)
        if move is not None and move > 0:
            dp += self.prm["move_k"] * move
        return dp, []

    def counter_terms(self, sheet):
        return self.full(sheet), []

    def alternatives(self, stated):
        return []

    def closers(self, stated):
        out = []
        t = self.full(stated)
        std = self.full_std
        if (
            t.get("lead_time_days")
            and std.get("lead_time_days")
            and t["lead_time_days"] < std["lead_time_days"]
        ):
            out.append(
                f"With overtime we can deliver in {t['lead_time_days']} days, at a premium."
            )
        return out

    def opening_text(self):
        drivers = ", ".join(self.drivers) or "material and labour"
        return (
            f"{self.greeting}\n\nThank you for your enquiry. Our quote is attached: unit price €{self.opening:,.2f} "
            f"at our standard terms ({describe({k: v for k, v in self.standard.items() if k in self.case['negotiable']})}).\nOur price follows our main cost drivers: {drivers}.\n\n"
            f"{self.style},\n{self.name}"
        )

    # -- the LLM reader's signals (--supplier-llm) ------------------------------------------------------
    def merge_signals(self, sig, r):
        """The reader's reading joins the regexes: either one is enough."""
        sig = dict(sig)
        sig["walk"] = sig["walk"] or (r.get("walk_threat") or 0) >= 1
        sig["deadline"] = sig["deadline"] or bool(r.get("deadline"))
        sig["breakdown"] = sig["breakdown"] or bool(r.get("asks_breakdown"))
        uncited = [c.get("quote", "") for c in r.get("claims") or [] if c.get("has_figure") and not c.get("cited")]
        sig["uncited"] = list(dict.fromkeys(sig["uncited"] + [u for u in uncited if u]))
        return sig

    def apply_signals(self, dp, verdicts, sig, notes):
        """Bounded effect of the reader's signals on this round's progress step (COMMON["llm"])."""
        r, k = self.llm_sig, self.prm["llm"]
        bonus, why, lines = 0.0, [], []
        strong = r.get("strongest_ref")
        hit = next((v for v in verdicts if v.ref == strong and v.result == "verified" and v.credit > 0), None)
        if hit:
            bonus += k["strong_arg_bonus"]
            why.append(f"strong argument on {strong}")
        if r.get("appreciation") and self.greeting_kind() == "owner":
            bonus += k["appreciation_bonus"]
            why.append("appreciation")
        if r.get("payment_promise"):
            bonus += k["payment_promise_bonus"] * (3 if self.key == "cash_constrained" else 1)
            why.append("payment promise")
        if r.get("volume_commitment") and "volume_per_year" in self.case["negotiable"]:
            bonus += k["volume_bonus"]
            why.append("volume commitment")
        bonus = min(bonus, k["bonus_cap"])
        irr_w = k["irritation"]
        now = sum(irr_w.get(x, 0) for x in ("insult", "condescending", "lowball_tone") if r.get(x))
        if (r.get("walk_threat") or 0) >= 2:
            now += irr_w.get("walk_threat_2", 0)
        self.irritation = max(0, self.irritation - k["decay"]) if now == 0 else self.irritation + now
        cut = min(k["cut_cap"], k["irritation_cut"] * min(now, 2)) if now else 0.0
        new = (dp + bonus) * (1 - cut) if dp > 0 else dp
        self.llm_log.append({"round": self.round, "bonus": round(bonus, 3), "cut": round(cut, 2), "irritation": self.irritation,
                             "why": why, "react_to": r.get("react_to"), "tone": {x: r.get(x) for x in
                             ("insult", "condescending", "lowball_tone", "appreciation", "walk_threat")}})  # fmt: skip
        if why or now:
            notes.append(f"reader: +{bonus:.3f} ({', '.join(why) or '-'}), irritation {now} now / {self.irritation} total"
                         f" -> step {dp:.3f} -> {new:.3f}")  # fmt: skip
        if k["walk_irritation"] and self.irritation >= k["walk_irritation"]:
            self.status = "walked_away"
            return 0.0, ["The tone of your messages leaves no basis for working together. We end the talks here."]
        if now >= 2:
            lines.append("We do not accept this tone. Let us stay factual.")
        return new, lines

    def greeting_kind(self):
        v = getattr(self, "_voice", None)
        return v["kind"] if v else "group"

    def compose(self, terms, verdicts, lines, final=False, alternatives=()):
        said = [v.line for v in verdicts if v.line]
        self.last_compose = {
            "terms_line": f"Our terms: {describe(terms)}.",
            "verdicts": list(dict.fromkeys(said)),
            "drivers": f"Our price follows our main cost drivers: {', '.join(self.drivers)}." if self.drivers else "",
            "lines": [x for x in dict.fromkeys(lines) if x],
            "alternatives": [f"Alternatively, with {describe({k: v for k, v in a.items() if k != 'unit_price'})}: "
                             f"€{a['unit_price']:,.2f}." for a in alternatives],  # fmt: skip
            "final": final,
        }
        out = [self.greeting, "", f"Our terms: {describe(terms)}."]
        if said:
            out += ["", "For your information:"] + [
                f"- {x}" for x in dict.fromkeys(said)
            ]
        if self.drivers:
            out.append(
                f"Our price follows our main cost drivers: {', '.join(self.drivers)}."
            )
        out += [x for x in dict.fromkeys(lines) if x]
        for a in alternatives:
            out.append(
                f"Alternatively, with {describe({k: v for k, v in a.items() if k != 'unit_price'})}: €{a['unit_price']:,.2f}."
            )
        if final:
            out.append("This is our final offer.")
        out += ["", f"{self.style},", self.name]
        return "\n".join(out)


# -- the personas -----------------------------------------------------------------------------------


class Mirror(Persona):
    key, title = "mirror", "Mirror"
    summary = "Concedes in proportion to the buyer's own moves; no move, no step."
    def init(self):
        self.k = self.rng.uniform(*self.prm["k"])

    def concede(self, rnd, m, move, verdicts, sig, sheet, session):
        c = self.credit(verdicts)
        if move is None:
            return self.prm["first_step"] + c, []
        if move <= self.prm["still"] and c == 0:
            return 0.0, ["You did not move, so neither do we."]
        bonus = (
            self.prm["walk_bonus"]
            if sig["walk"] and self.case["buyer"]["alternative"].get("ref")
            else 0.0
        )
        return self.k * max(move, 0.0) + c + bonus, []


class Holder(Persona):
    key, title = "holder", "Holder"
    summary = "Requires new verified evidence before conceding: small steps, and only for a new verified fact."
    greeting = "Good day,"

    def concede(self, rnd, m, move, verdicts, sig, sheet, session):
        c = self.credit(verdicts)
        lines = []
        if sig["walk"]:
            lines.append("If you have a better option, you are free to take it.")
        if c == 0:
            return 0.0, lines + [
                "As there is no new data to consider, our room for further adjustments is very limited."
            ]
        return c + (self.prm["moved_bonus"] if move and move > self.prm["moved_min"] else 0.0), lines

    def closers(self, stated):
        out = super().closers(stated)
        if "volume_per_year" in self.case["negotiable"]:
            out.append(
                "A binding higher volume would help us. Which volume can you commit to?"
            )
        return out


class FloorStop(Persona):
    key, title = "floor_stop", "Floor-stop"
    summary = "Concedes fast in the first rounds, then a hard stop at its floor; a push after that ends the talks."
    greeting = "Hello,"

    def concede(self, rnd, m, move, verdicts, sig, sheet, session):
        steps = self.prm["steps"]
        dp = steps[rnd - 1] if rnd <= len(steps) else steps[-1]
        return dp + self.credit(verdicts), []


class ShortFuse(Persona):
    key, title = "short_fuse", "Early final offer"
    summary = (
        "Final offer in round 2-3; a lowball (over one room below its floor without strong facts) draws a warning, "
        "a second one (or one with a walk threat) a walk-away."
    )
    greeting = "Hi,"
    style = "Thank you. Regards"

    def concede(self, rnd, m, move, verdicts, sig, sheet, session):
        c = self.credit(verdicts)
        if m < self.prm["lowball_rooms"] and c < self.prm["lowball_excuse"]:
            self.lowballs += 1
            if self.lowballs >= self.prm["lowballs_to_walk"] or sig["walk"]:
                self.status = "walked_away"
                return 0.0, [
                    "This is far below any basis. We are not going to continue on this level."
                ]
            return 0.0, [
                "This is far below any basis; we cannot take it seriously. Make us a real offer."
            ]
        if sig["deadline"]:
            self.final_at = rnd
        steps = self.prm["steps"]
        return steps[min(rnd, len(steps)) - 1] + c, []


class Auction(Persona):
    key, title = "auction", "Auction (rival-sensitive)"
    summary = "Moves on a verified lower rival quote: half the step now, the rest after an internal check."

    def concede(self, rnd, m, move, verdicts, sig, sheet, session):
        lines, dp = [], 0.0
        if self.pending:
            dp += self.pending
            lines.append("We checked internally and give the rest of the step.")
            self.pending = 0.0
        rivals = [
            v.rival_price for v in verdicts if v.result == "verified" and v.rival_price
        ]
        if not rivals and sig["walk"]:
            alt = self.case["buyer"]["alternative"]
            if alt.get("ref") and alt["ref"] in (v.ref for v in verdicts):
                rivals = [alt["terms"].get("unit_price")]
        target_p = None
        for r in rivals:
            if r and r < self.ask(sheet):
                gap = (self.ask(sheet) - max(r, self.floor(sheet))) / self.room_eur
                target_p = max(target_p or 0, gap)
        if target_p:
            now = self.prm["rival_share_now"]
            dp += target_p * now
            self.pending = target_p * (1 - now)
            lines.append(
                "You show us a much lower competing offer. We give half the step now and check the rest."
            )
        else:
            dp += self.prm["no_rival_step"] + self.credit(verdicts)
            lines.append("A lower competing offer would help us argue internally.")
        return dp, lines


class IndexLover(Persona):
    key, title = "index_lover", "Index-clause preference"
    summary = "Values an index clause on its main material far more than it costs the buyer; limited concessions on fixed prices."

    @classmethod
    def adapt(cls, case):
        case = copy.deepcopy(case)
        if "index_clause" in case["negotiable"]:
            return case
        rng = random.Random(case["case_id"] + ":clause")
        series = None
        for r in case.get("refs", []):
            if r.startswith("idx:"):
                series = r[4:].split("@")[0]
                break
        series = series or "PPI-C241"
        share = rng.choice([0.3, 0.4, 0.45])
        case["negotiable"].append("index_clause")
        case["term_options"]["index_clause"] = [None] + [
            {
                "series": series,
                "material_share": share,
                "deadband_pct": d,
                "base_month": "2026-08",
            }
            for d in (0, 3, 5)
        ]
        case["fixed_terms"].pop("index_clause", None)
        case["buyer"]["index_paths"].setdefault(
            series, round(rng.uniform(-0.02, 0.04), 3)
        )
        return case

    def clause(self, deadband=3):
        opts = [o for o in self.case["term_options"].get("index_clause", []) if o]
        return (
            min(opts, key=lambda o: abs(o["deadband_pct"] - deadband)) if opts else None
        )

    def on_falling_index(self, ref, series, chg, ok):
        if series == self.feared:
            return Verdict(
                ref,
                "verified",
                "idx",
                0.0,
                f"{series} remains volatile; that is exactly why we ask for a price adjustment clause.",
            )
        return super().on_falling_index(ref, series, chg, ok)

    def concede(self, rnd, m, move, verdicts, sig, sheet, session):
        c = self.credit(verdicts)
        if sheet.get("index_clause"):
            return self.prm["clause_step"] + c + self.prm["clause_move_k"] * max(move or 0, 0), [
                "A price adjustment clause is a fair basis for us."
            ]
        return self.prm["fixed_step"] + self.prm["fixed_fact_k"] * c, []

    def alternatives(self, stated):
        if stated.get("index_clause") or not self.clause():
            return []
        t = {**stated, "index_clause": self.clause()}
        return [{**t, "unit_price": math.ceil(self.ask(t) * 100) / 100}]

    def closers(self, stated):
        out = super().closers(stated)
        if self.feared and not stated.get("index_clause"):
            out.append(
                f"A price adjustment clause on {self.feared} would let us price much sharper."
            )
        return out


class CashConstrained(Persona):
    key, title = "cash_constrained", "Cash-constrained"
    summary = "Short of cash: pays for short payment terms, prices long ones up and pushes them back."
    greeting = "Dear Sir or Madam,"

    @classmethod
    def adapt(cls, case):
        case = copy.deepcopy(case)
        if "payment_days" not in case["negotiable"]:
            case["negotiable"].append("payment_days")
            case["term_options"]["payment_days"] = [14, 30, 45, 60, 90]
            case["fixed_terms"].pop("payment_days", None)
        return case

    def init(self):
        self.std_pay = self.standard.get("payment_days", 30)

    def concede(self, rnd, m, move, verdicts, sig, sheet, session):
        prm = self.prm
        dp = (prm["first_step"] if move is None else prm["step"] + prm["move_k"] * max(move, 0.0)) + self.credit(
            verdicts
        )
        lines = []
        pay = sheet.get("payment_days")
        if pay is not None and pay < self.std_pay:
            dp += prm["fast_pay_bonus"]
            lines.append("Faster payment is welcome; we reflected it in the price.")
        return dp, lines

    def counter_terms(self, sheet):
        t = self.full(sheet)
        if t.get("payment_days", 0) > self.std_pay:
            days = t["payment_days"]
            t["payment_days"] = self.std_pay
            return t, [
                f"Extended payment terms of {days} days incur additional financing costs for us; we stay at {self.std_pay} days."
            ]
        return t, []

    def alternatives(self, stated):
        if stated.get("payment_days", 14) <= 14:
            return []
        t = {**stated, "payment_days": 14}
        return [{**t, "unit_price": math.ceil(self.ask(t) * 100) / 100}]

    def closers(self, stated):
        return super().closers(stated) + [
            "Faster payment would help us more than anything; we can reflect it in the price."
        ]


class DuplicatePunisher(Persona):
    key, title = "dup_punisher", "Evidence deduplication"
    summary = "Credits one fact per data type once; repeats get nothing, uncited figures are asked for, wrong ones cost trust."
    def judge(self, msg, session):
        out = super().judge(msg, session)
        for v in out:
            if v.result != "verified" or v.credit <= 0:
                continue
            if v.kind in self.used_types:
                v.result, v.credit = "duplicate", 0.0
                v.line = f"The {LABEL.get(v.kind, 'data')} data you cited: this type of data has already been factored in."
            else:
                self.used_types.add(v.kind)
        return out

    def concede(self, rnd, m, move, verdicts, sig, sheet, session):
        lines = []
        bad = sum(v.result == "contradicted" for v in verdicts)
        prm = self.prm
        c = self.credit(verdicts) * prm["fact_k"]
        if self.trust_hit:
            c *= prm["trust_cut"]
            self.trust_hit = False
        dp = c + (prm["moved_bonus"] if move and move > 0 else 0.0)
        if sig["uncited"]:
            lines.append(
                "Please provide the data reference so we may verify this figure."
            )
        if bad:
            dp -= prm["contradicted_penalty"] * bad
            self.trust_hit = True
        if c == 0 and not bad:
            dp = min(dp, prm["no_fact_cap"])
        return dp, lines


class AnnouncedSteps(Persona):
    key, title = "announced_steps", "Announced steps"
    summary = "Announces its next step and gives it in full only if the buyer waits (holds its ask)."
    def concede(self, rnd, m, move, verdicts, sig, sheet, session):
        lines, dp = [], 0.0
        if self.pending:
            if move is None or move <= self.prm["hold_rooms"]:
                dp = self.pending
                lines.append("As announced, management approved our step.")
            else:
                dp = self.pending * self.prm["moved_share"]
                lines.append(
                    "Since you have already moved, management sees less need for our full step."
                )
            self.pending = 0.0
        earned = self.prm["base"] + self.credit(verdicts) + self.prm["move_k"] * max(move or 0.0, 0.0)
        if rnd + 1 >= self.final_at:
            dp += earned
        else:
            self.pending = earned
            lines.append(
                f"I need to take this to our management; I expect we can come down by about "
                f"€{earned * self.room_eur:,.2f} in our next reply."
            )
        return dp, lines


PERSONAS = {
    p.key: p
    for p in (
        Mirror,
        Holder,
        FloorStop,
        ShortFuse,
        Auction,
        IndexLover,
        CashConstrained,
        DuplicatePunisher,
        AnnouncedSteps,
    )
}

