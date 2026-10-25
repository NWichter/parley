"""When to accept, counter or walk, and what to ask for. Works in the buyer's total cost C and in an
estimate of what each package is worth to the supplier; prices are only ever solved for, never
guessed."""

import copy
import json
import math
import re
import threading
import time
from dataclasses import dataclass, field

from . import rules
from .analyst import SIGNALS, Reading, merge, read_reply
from .dossier import MATERIAL_SERIES, Claim, Dossier, pf_on, signal, still_valid
from .kit.cost import closing_terms
from .kit.docs import quote_terms, standard_terms
from .valuation import SupplierModel, Valuation

ENDED = {"walked_away", "no_bid"}


def _env(name, default, cast=float):
    raw = (rules.env(f"PARLEY_{name}") or "").strip()
    return cast(raw) if raw else default

DEPTH = {"leverage": 0.16, "routine": 0.12, "quality_first": 0.10, "stabilise": 0.08, "secure_supply": 0.06}
MAX_DEPTH = 0.22
RFQ_DEPTH, RFQ_MAX_DEPTH = 0.08, 0.26
CURRENT_PRICE_CAP = 1.02
RECIPROCITY = _env("RECIPROCITY", 0.6)
MIN_STEP = _env("MIN_STEP", 0.05)
MAX_STEP = _env("MAX_STEP", 0.25)
HOLD_STEP = 0.0
BIG_STEP, AFTER_BIG_STEP = 0.4, _env("AFTER_BIG_STEP", 0.15)
MOVING_STEP, KEEP_GAP = 0.005, 0.005
FIRST_FACTS = _env("FIRST_FACTS", 2, int)
SHOULD_COST_CLAMP = _env("SHOULD_COST_CLAMP", 0.94)
CASH_RATE = _env("CASH_RATE", 0.10)
LEVER_ASK = 0.01
RFQ_TAKE_GAP = _env("RFQ_TAKE_GAP", 0.04)
RFQ_LEAD = 0.05
RFQ_DROP = 0.08
TYPE_DEPTH, TYPE_MAX_DEPTH = 0.06, 0.24
LATE_SHARE = 0.005
PI_MIN_STEP = 0.20
ACCEPT_GAP = 0.0025
STALE_FINAL_S = 25
FINAL_WAIT_S = 30
FOLLOW_TOLERANCE = 0.006
ZERO_GUARD = 0.10
ASK_ROOM_SHARE, ASK_PRICE_SHARE = 0.35, 0.04
FROZEN = re.compile(r"\bfrozen\b|price freeze|freezes? (?:all )?price", re.I)
CORPORATE_ROLE = re.compile(r"^\s*(?:Key Account Manager|Inside Sales|Segment Manager)\b", re.I | re.M)
PRICE_KINDS = {"po", "index", "should_cost"}
CLAUSE_MARGIN, INDEX_CAP = 1.2, 0.08
V1_RFQ_DEPTH = 0.04
TRAILING = re.compile(
    r"\b([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+)\s+(rose|fell|increased|decreased|climbed|dropped)\s+(?:by\s+)?"
    r"(\d+(?:[.,]\d+)?)\s*%\s+in\s+(?:the\s+last\s+)?(?:twelve|12)\s+months",
    re.I,
)

TAPER_STEP = 0.003
W_FLOOR = _env("W_FLOOR_SHARE", 0.0025)
FLOOR_TALK = re.compile(
    r"bottom line|absolute (?:best|limit|minimum)|(?:best|lowest) (?:possible )?price|cannot go (?:any )?lower|"
    r"can(?:not|'t) (?:drop|reduce|lower) (?:it|the price|our price)?\s*(?:any )?further|our limit|"
    r"that's it|no further room|we are at our floor",
    re.I,
)
ENERGY_NAME = re.compile(r"energy|electric|power|gas|labou?r|conversion|wage", re.I)
ENERGY_SERIES = {s for k in ("electric", "power", "energy", "gas") for s in MATERIAL_SERIES[k]}


def on(name):
    """Candidate switches, off unless PARLEY_<NAME>=1."""
    return rules.env(f"PARLEY_{name}") == "1"


FORK_SHARE = 0.95
CLAUSE_ASK = re.compile(r"(price[- ]adjustment|index(ation)?|escalation|material) clause|index-linked|indexed pric", re.I)
MOVER_STEP = 0.15
UNINFORMED_FACTS = 3
PROBE_ORDER = (
    "payment_days",
    "index_clause",
    "lead_time_days",
    "chargeback_eur_per_complaint",
    "ppm_commitment",
    "effective_date",
)


# Candidate switches that passed the gate: on unless the environment sets them to 0.
ADOPTED = {"PARLEY_HEDGE_NOCLAUSE": "1", "PARLEY_REL_NO_BREAKDOWN_COST": "1", "PARLEY_CASH_STRICT": "1", "PARLEY_V1_RFQ": "rival", "PARLEY_RFQ_CLOSE_NOW": "1", "PARLEY_RFQ_ORDERED": "1", "PARLEY_HEDGE_DOCS": "1", "PARLEY_ROUND_GUARD": "1"}


def flag(name, part=None):
    """Candidate switches, default off (ADOPTED: default on): NAME=1 (or all) turns every part on,
    NAME=a,b only those parts, NAME=0 off."""
    raw = rules.env(name)
    raw = (ADOPTED.get(name, "") if raw is None else raw).strip().lower()
    if raw in ("", "0", "false", "no", "off", "none"):
        return False
    if part is None or raw in ("1", "true", "yes", "on", "all"):
        return True
    return part in {x.strip() for x in raw.split(",")}


def depth_add(goal):
    """PARLEY_DEPTH_ADD="stabilise:0.03,secure_supply:0.03" (or "all:0.02") deepens the first ask for those goals."""
    raw = (rules.env("PARLEY_DEPTH_ADD") or "").strip()
    out = 0.0
    for part in raw.split(","):
        key, _, val = part.partition(":")
        if key.strip() in (goal, "all"):
            try:
                out = float(val)
            except ValueError:
                pass
    return out


def type_depth():
    """PARLEY_TYPE_DEPTH=<float> overrides TYPE_DEPTH (for an ablation); unset keeps it."""
    raw = (rules.env("PARLEY_TYPE_DEPTH") or "").strip()
    try:
        return float(raw) if raw else TYPE_DEPTH
    except ValueError:
        return TYPE_DEPTH


QUALITY_PREFIXES = ("sc:", "qn:")


def quality_claim(c):
    return bool(c.refs) and all(r.startswith(QUALITY_PREFIXES) for r in c.refs)


def trailing_moves(msg):
    """Series moves a supplier states for the last twelve months, as fractions (rose 5.9 % -> 0.059)."""
    out = {}
    for c in msg.get("claims") or []:
        pct = (c.get("asserted") or {}).get("change_pct")
        refs = c.get("refs") or []
        if c.get("type") == "index_change" and pct is not None and refs:
            out[refs[0].split(":", 1)[-1].split("@")[0]] = float(pct) / 100
    for m in TRAILING.finditer(msg.get("body") or msg.get("text") or ""):
        sign = -1 if m.group(2).lower() in ("fell", "decreased", "dropped") else 1
        out.setdefault(m.group(1), sign * float(m.group(3).replace(",", ".")) / 100)
    return out
CLOSING_STEP, MAX_FACTS = 0.5, 3


@dataclass
class Decision:
    kind: str  # send | accept | walk | done | hold
    supplier_id: str
    sheets: list = field(default_factory=list)
    claims: list = field(default_factory=list)
    lines: dict = field(default_factory=dict)
    reason: str = ""
    metrics: dict = field(default_factory=dict)
    term_sheet_id: str | None = None


@dataclass
class ThreadView:
    sid: str
    events: list
    status: str
    sheet_id: str | None
    terms: dict
    std: dict
    c_standing: float
    moves_left: int
    asks: list
    offers: list
    used_refs: set
    reading: Reading
    alternatives: list
    replied_at: float | None = None
    c_plain: float = math.inf

    @property
    def alive(self):
        return self.status not in ENDED and self.sheet_id is not None

    @property
    def rounds(self):
        return len(self.asks)


def supplier_messages(events):
    return [m for m in events if m.get("from") != "buyer"]


def buyer_messages(events):
    return [m for m in events if m.get("from") == "buyer" and not m.get("action")]


ID_RE = re.compile(r"\b(?:idx|quote|offer|sc|qn|po|cost|fc|news|doc|ctr):[\w.:@#/-]*\w")


class CasePolicy:
    def __init__(self, case, dossier: Dossier, clock=time.time):
        self.case, self.dossier, self.clock = case, dossier, clock
        if flag("PARLEY_INDEX_VALUE"):
            paths = dict(case["buyer"].get("index_paths") or {})
            self.val = Valuation(copy.copy({**case, "buyer": {**case["buyer"], "index_paths": paths}}))
        else:
            self.val = Valuation(case)
        self.buyer = case["buyer"]
        self.goal = self.buyer.get("category_goal", "leverage")
        self.rfq = len(case["suppliers"]) > 1
        self.models: dict[str, SupplierModel] = {}
        self.claim_bank = {s: dossier.claims(s) for s in case["suppliers"]}
        self.dead_kinds: dict[str, set] = {s: set() for s in case["suppliers"]}
        self.replied_at: dict[str, float] = {}
        self.anchor_value: dict[str, float] = {}
        self.floor: dict[str, float] = {}
        self.reserved: set = set()
        self.extra: dict = {}
        self.frozen: set = set()
        self.forks: dict = {}
        self.parked: set = set()
        self.commit_offered: set = set()
        self.freeze_offered: set = set()
        self.time_rounds: dict[str, int | None] = {}
        self.claims_lock = threading.Lock()

    # ---- views ------------------------------------------------------------------------------

    def cost(self, sid, terms, std=None):
        return self.val.cost(terms, std, self.models.get(sid))

    def clause_cost(self, terms, std=None):
        """What the index clause on these terms is expected to cost us (published cost, no health term)."""
        if not terms.get("index_clause"):
            return 0.0
        return self.val.cost(terms, std) - self.val.cost({**terms, "index_clause": None}, std)

    def learn_paths(self, events):
        """A clause series the case gives no path for: the supplier's stated twelve-month move, capped."""
        paths = self.val.buyer.setdefault("index_paths", {})
        series = {o["series"] for o in self.val.options("index_clause") if isinstance(o, dict) and o.get("series")}
        for msg in supplier_messages(events):
            for s, move in trailing_moves(msg).items():
                if s in series and s not in paths:
                    paths[s] = max(-INDEX_CAP, min(INDEX_CAP, move))

    def model(self, sid, events):
        m = self.models.get(sid)
        if m is None:
            prof = self.dossier.profiles[sid]
            opening = (supplier_messages(events)[0].get("term_sheets") or [{}])[0].get("terms", {})
            rate = prof.interest_rate
            sup0 = supplier_messages(events)[0].get("body") or ""
            if signal("type") and not prof.cash_short and CORPORATE_ROLE.search(sup0[-400:]):
                rate = min(rate, 0.065)
            full = prof.full_cost
            if full and flag("PARLEY_FC_CAP") and opening.get("unit_price"):
                full = min(full, float(opening["unit_price"]))
            m = SupplierModel(interest_rate=rate, full_cost=full)
            self.models[sid] = m
            m.opening = dict(opening)
            if flag("PARLEY_INDEX_VALUE"):
                self.learn_paths(events)
        std = standard_terms(events)
        merged = {k: v for k, v in getattr(m, "opening", {}).items() if k != "unit_price"}
        merged.update({k: v for k, v in std.items() if v is not None or k not in merged})
        master = self.dossier.master_terms(sid)
        plan = closing_terms(self.case, {"unit_price": 1.0})
        for k in ("payment_days", "incoterm", "lead_time_days"):
            if merged.get(k) is None:
                merged[k] = master.get(k) if master.get(k) is not None else plan.get(k)
        m.standard = merged
        if on("PAY_RATE"):
            r = implied_rate(events, merged.get("payment_days"))
            if r is not None:
                m.interest_rate = r
        return m

    def view(self, sid, events) -> ThreadView:
        sup = supplier_messages(events)
        last = sup[-1] if sup else {}
        sheets = last.get("term_sheets") or []
        sheet_id, terms = (sheets[0]["term_sheet_id"], sheets[0]["terms"]) if sheets else (None, {})
        std = standard_terms(events)
        c = self.cost(sid, terms, std) if sheet_id else math.inf
        asks = [m["term_sheets"] for m in buyer_messages(events) if m.get("term_sheets")]
        offers = []
        for m in sup:
            ts = m.get("term_sheets") or []
            if ts:
                offers.append((ts[0]["terms"], self.cost(sid, ts[0]["terms"], std)))
        used = set()
        for m in buyer_messages(events):
            used.update(m.get("refs") or [])
            used.update(ID_RE.findall(m.get("body") or ""))
        reading = read_reply(last) if last else Reading(status="open")
        extra = self.extra.get((sid, len(sup)))
        if extra:
            reading = merge(reading, extra)
        alts = []
        for s in reading.alternatives:
            alts.append((s.get("term_sheet_id"), s["terms"], self.cost(sid, s["terms"], std)))
        moves_left = self.case.get("round_limit", 10) - sum(
            1 for m in events if m.get("from") == "buyer" and not m.get("action")
        )
        return ThreadView(
            sid,
            events,
            last.get("status") or "open",
            sheet_id,
            terms,
            std,
            c,
            moves_left,
            asks,
            offers,
            used,
            reading,
            alts,
            self.replied_at.get(sid),
            self.val.cost(terms, std) if sheet_id else math.inf,
        )

    def c_open(self, views):
        best = math.inf
        for v in views.values():
            sup = supplier_messages(v.events)
            ts = (sup[0].get("term_sheets") or []) if sup else []
            if ts and self.val.keeps_must_haves(ts[0]["terms"], v.std):
                best = min(best, self.cost(v.sid, ts[0]["terms"], v.std))
        return best

    def c_open_plain(self, views):
        best = math.inf
        for v in views.values():
            sup = supplier_messages(v.events)
            ts = (sup[0].get("term_sheets") or []) if sup else []
            if ts:
                best = min(best, self.val.cost(ts[0]["terms"], v.std))
        return best

    def acceptable(self, v: ThreadView, c_open=math.inf):
        """An offer we may take: it keeps every must-have, beats the alternative and costs less than
        the cheapest opening (a deal at or above the opening scores nothing)."""
        return bool(
            v.alive
            and v.sheet_id
            and self.val.keeps_must_haves(v.terms, v.std)
            and self.c_vs_alt(v) < self.val.c_alt
            and v.c_standing < c_open
        )

    def c_vs_alt(self, v: ThreadView):
        """Our cost of the standing offer for the comparison with the alternative. The alternative is
        priced without a supplier-health charge; with PARLEY_REL_PLAIN_ACCEPT the offer is too (like
        for like), while the opening check stays health-aware."""
        return v.c_plain if flag("PARLEY_REL_PLAIN_ACCEPT") else v.c_standing

    def est_score(self, sid, v, views):
        """A rough case score of the standing offer: against the cheapest opening, with the best
        reachable deal guessed as the standing structure at our floor estimate (or the alternative)."""
        c_open = self.c_open(views)
        fl = self.floor_price(sid, v)
        c_best = self.val.c_alt
        if fl and v.sheet_id:
            c_best = min(c_best, self.cost(sid, {**v.terms, "unit_price": fl}, v.std))
        if not (c_open < math.inf and c_best < c_open):
            return None
        return (c_open - v.c_standing) / (c_open - c_best)

    def zero_guard(self, sid, v, views):
        """True while the standing offer would score next to nothing and we still have time and
        something to give: then we keep negotiating instead of taking it."""
        if v.status != "open" or v.moves_left < 2:
            return False
        est = self.est_score(sid, v, views)
        if est is None or est >= ZERO_GUARD:
            return False
        return self.has_fresh_fact(sid, v) or self.unused_lever(sid, v, self.value(sid, v.terms)) is not None

    def best_standing(self, views):
        c_open = self.c_open(views)
        cands = [v for v in views.values() if self.acceptable(v, c_open)]
        return min(cands, key=lambda v: v.c_standing) if cands else None

    # ---- the anchor and the curve -----------------------------------------------------------

    def standing_price(self, v):
        return float(v.terms.get("unit_price") or 0)

    def min_price(self, v):
        """No sheet ever goes below half the supplier's opening price (and never to zero)."""
        sup = supplier_messages(v.events)
        opening = ((sup[0].get("term_sheets") or [{}])[0].get("terms") or {}).get("unit_price") if sup else None
        return max(0.01, 0.5 * float(opening or self.standing_price(v) or 0))

    def floor_price(self, sid, v):
        """Our estimate of the lowest price the supplier can sign: its cost breakdown or our
        should-cost, never above the lowest price it has already offered (it does not sell below cost)."""
        prof = self.dossier.profiles[sid]
        est = self.floor.get(sid) or prof.should_cost
        offered = [float(t["unit_price"]) for t, _ in v.offers if t.get("unit_price")]
        if offered:
            low = min(offered)
            est = min(est, low) if est else None
        cur = self.buyer.get("current_unit_price")
        if est and cur:
            est = min(est, cur * CURRENT_PRICE_CAP)
        return est

    def anchor_price(self, sid, v):
        opening = float(supplier_messages(v.events)[0]["term_sheets"][0]["terms"]["unit_price"])
        v1_depth = self.rfq and flag("PARLEY_V1_RFQ", "depth")
        depth = DEPTH.get(self.goal, 0.12) + ((V1_RFQ_DEPTH if v1_depth else RFQ_DEPTH) if self.rfq else 0.0)
        depth += depth_add(self.goal)
        if self.case.get("type") in ("bundling", "raw_material"):
            depth = min(depth + type_depth(), TYPE_MAX_DEPTH)
        target = opening * (1 - depth)
        cur = self.buyer.get("current_unit_price")
        if self.case.get("type") in ("price_increase", "raw_material") and cur and cur < opening:
            target = min(target, cur * 0.995)
        fl = self.floor_price(sid, v)
        if fl and fl < opening:
            target = max(target, fl * SHOULD_COST_CLAMP)
        cap = (
            (MAX_DEPTH if v1_depth else RFQ_MAX_DEPTH)
            if self.rfq
            else (TYPE_MAX_DEPTH if self.case.get("type") in ("bundling", "raw_material") else MAX_DEPTH)
        )
        return max(target, opening * (1 - cap))

    def beta(self, sid, v: ThreadView, prev_gap_value, gap_value, fresh_fact=False):
        """Our next step as a share of the gap, matched to the supplier's last step: we move when it
        moves and hold (with a new fact) when it holds."""
        r = v.reading
        if r.flags.get("too_low"):
            return 0.5, "the supplier calls our ask too far from any basis"
        if r.flags.get("walk_threat"):
            return 0.35, "the supplier hints it may end the talks: a clear step"
        if r.flags.get("step_announced"):
            return HOLD_STEP, "the supplier announced an internal step: hold"
        if gap_value <= 0:
            return 0.0, "no gap"
        step = 0.0
        if len(v.offers) >= 2:
            step = self.value(sid, v.offers[-2][0]) - self.value(sid, v.offers[-1][0])
        if step <= 0.001 * max(1e-9, self.value(sid, v.terms)):
            if r.flags.get("wants_move"):
                return 0.2, "the supplier waits for movement from us"
            if fresh_fact:
                return HOLD_STEP, "the supplier held: we hold and bring a new fact"
            return 0.1, "the supplier held and we have no new fact: a small step"
        if prev_gap_value and step > BIG_STEP * prev_gap_value:
            return AFTER_BIG_STEP, f"the supplier closed {step / prev_gap_value:.0%} of the gap: a small reply"
        share = RECIPROCITY * step / gap_value
        low = MOVER_STEP if pf_on("mover") and self.mover(v) else MIN_STEP
        b = min(MAX_STEP, max(low, share))
        if self.case.get("type") == "price_increase" and b < PI_MIN_STEP:
            return (
                PI_MIN_STEP,
                f"the supplier moved {step:.2f} per unit on its increase: a regular {PI_MIN_STEP:.0%} step",
            )
        return b, f"the supplier moved {step:.2f} per unit: we answer with {b:.0%} of the gap"

    # ---- packages ---------------------------------------------------------------------------

    def candidate_combos(self, sid, v):
        m = self.models[sid]
        combos = [c for c in self.val.combos(m) if self.val.valid_combo(c, v.std)]
        if on("NO_CLAUSE") and self.clause_costs_us(v):
            combos = [c for c in combos if not c.get("index_clause")] or combos
        need = self.buyer.get("need_lead_time_days")
        if need and "lead_time_days" in self.val.non_price_fields():
            combos = [c for c in combos if c["lead_time_days"] >= need] or combos
        return combos or [self.val.combo_of(v.terms)]

    def clause_costs_us(self, v):
        """PARLEY_NO_CLAUSE: the opening sheet has no index clause and every clause option on the
        table costs us money (its series is expected to rise beyond the deadband)."""
        if "index_clause" not in self.case.get("negotiable", []):
            return False
        if self.wants_clause(v):
            return False
        base = {**v.terms, "index_clause": None}
        opts = [o for o in self.val.options("index_clause") if o]
        return bool(opts) and all(self.clause_cost({**base, "index_clause": o}, v.std) > 0 for o in opts)

    def wants_clause(self, v):
        """The supplier asked for an index clause: one of its sheets (or alternative sheets) carries
        one, or one of its mails asks for a price adjustment clause."""
        for m in supplier_messages(v.events):
            if any((ts.get("terms") or {}).get("index_clause") for ts in m.get("term_sheets") or []):
                return True
            if CLAUSE_ASK.search(m.get("body") or ""):
                return True
        return any(a[1].get("index_clause") for a in v.alternatives)

    def value(self, sid, terms):
        m = self.models[sid]
        return self.val.supplier_value(terms, m)

    def packages(self, sid, v, target_value, n_alt=2):
        """Up to three sheets. The first is cheapest for us at the value we offer the supplier (on the
        structure the supplier chose, if that is about as good); the others cost us the same as the
        first and each differ in one term the supplier may value: payment, index clause, lead time."""
        m = self.models[sid]
        combos = self.candidate_combos(sid, v)
        priced = []
        for c in combos:
            p = self.val.price_for_value(c, target_value, m)
            if p is None or p < self.min_price(v):
                continue
            if self.val.relationship and m.full_cost:
                p = max(p, math.ceil(self.val.cheapest_price(c, v.std, m, 10 * p) * 100) / 100)
            if v.sheet_id:
                ceiling = self.val.price_for_value(c, self.value(sid, v.terms), m) - 0.01
                p = min(p, ceiling)

            terms = self.val.sheet(c, p)
            priced.append((self.cost(sid, terms, v.std), terms))
        if not priced:
            return []
        index_value = flag("PARLEY_INDEX_VALUE")
        margin = (CLAUSE_MARGIN - 1) if index_value else 0.0
        priced.sort(
            key=lambda x: (round(x[0] + margin * self.clause_cost(x[1], v.std), 0), -self._defer_rank(x[1]))
        )
        late = self.latest_effective_date(v)
        if late:
            priced = [x for x in priced if x[1].get("effective_date") == late] or priced
        if self.late_cost_matters(c_ref=priced[0][0], std_lead=m.standard.get("lead_time_days")):
            need = self.buyer["need_lead_time_days"]
            priced = [x for x in priced if x[1].get("lead_time_days", need) <= need] or priced
        if on("QUALITY_GUARD"):
            ref = v.terms if v.sheet_id else v.std
            priced = [x for x in priced if not self.loosens_quality(sid, v, x[1], ref)] or priced
        c_primary, primary = priced[0]
        chosen = self.chosen_combo(v)
        if chosen:
            for c, t in priced:
                if self.val.combo_of(t) == chosen and c <= c_primary * (1 + FOLLOW_TOLERANCE):
                    c_primary, primary = c, t
                    break
        sheets = [primary]
        pool = []
        c_plain = self.val.cost(primary, v.std)
        v_ceiling = self.value(sid, v.terms) if v.sheet_id else None
        for c in combos:
            p = self.val.price_for_cost(c, c_plain, v.std)
            if not p or p < self.min_price(v):
                continue
            if self.val.relationship and m.full_cost:
                p = max(p, math.ceil(self.val.cheapest_price(c, v.std, m, 10 * p) * 100) / 100)
            if v_ceiling is not None:
                p = min(p, self.val.price_for_value(c, v_ceiling, m) - 0.01)
            terms = self.val.sheet(c, p)
            if self.val.combo_of(terms) == self.val.combo_of(primary):
                continue
            same_price = {**terms, "unit_price": primary["unit_price"]}
            extra = self.clause_cost(same_price, v.std) - self.clause_cost(primary, v.std)
            if extra > 1e-6 and flag("PARLEY_V1_INDEX"):
                continue
            if extra > 1e-6 and index_value:
                p = self.val.price_for_cost(c, c_plain - margin * extra, v.std)
                if not p or p < self.min_price(v):
                    continue
                if v_ceiling is not None:
                    p = min(p, self.val.price_for_value(c, v_ceiling, m) - 0.01)
                terms = self.val.sheet(c, p)
                if self.cost(sid, terms, v.std) > c_primary - margin * extra:
                    continue
            if on("QUALITY_GUARD") and self.loosens_quality(sid, v, terms, primary):
                continue
            pool.append((self.value(sid, terms), terms))
        pool.sort(key=lambda x: -x[0])
        for f in PROBE_ORDER:
            if len(sheets) >= 1 + n_alt:
                break
            if f not in self.val.non_price_fields() or len(self.val.options(f)) < 2:
                continue
            for _, t in pool:
                if t in sheets or t[f] == primary[f]:
                    continue
                others = [k for k in self.val.non_price_fields() if k != f]
                if all(t[k] == primary[k] for k in others) or f == "index_clause":
                    if f == "payment_days" and not self._payment_probe(t, primary):
                        continue
                    sheets.append(t)
                    break
        for _, t in pool:
            if len(sheets) >= 1 + n_alt:
                break
            if t not in sheets and self._differs(t, sheets):
                sheets.append(t)
        if flag("PARLEY_ALT_GUARD"):
            sheets = [sheets[0]] + [t for t in sheets[1:] if not self.worse_terms(sid, v, t, primary)]
        if not v.asks and flag("PARLEY_HEDGE_NOCLAUSE"):
            sheets = [sheets[0]] + [
                t
                for t in sheets[1:]
                if t.get("index_clause") == primary.get("index_clause")
                or self.cost(sid, t, v.std) <= c_primary * (1 + 1e-9)
            ]
        cut = self.fork_cut(sid, v)
        self.forks[sid] = None
        if cut is not None and n_alt >= 1:
            lo = self.standing_price(v) * (1 - cut)
            fork = next((t for _, t in pool if t["unit_price"] >= lo - 1e-9), None)
            if fork is not None and primary["unit_price"] < lo - 1e-9:
                rest = [t for t in sheets[1:] if t != fork]
                sheets = [primary, fork] + rest[: n_alt - 1]
                self.forks[sid] = (fork, cut)
        limit = min(v.c_standing, self.c_open({sid: v}) if v.events else math.inf)
        valid = [t for t in sheets if self.val.keeps_must_haves(t, v.std)]
        keep = [t for t in valid if self.cost(sid, t, v.std) < limit]
        if not keep and flag("PARLEY_KEEP_VALID"):
            keep = valid
        return keep[: 1 + n_alt]

    def worse_terms(self, sid, v, t, primary):
        """PARLEY_ALT_GUARD: an alternative whose terms (at the primary's price) cost us more than the
        primary's. Suppliers pick the sheet worth most to them and end at much the same price on any
        structure, so such a sheet only moves the deal onto dearer terms. Parts: lead, clause, quality,
        pay (fields compared one at a time) or 1 for all."""
        fields = {
            "lead": ("lead_time_days",),
            "clause": ("index_clause",),
            "quality": ("ppm_commitment", "chargeback_eur_per_complaint"),
            "pay": ("payment_days",),
        }
        c0 = self.cost(sid, primary, v.std)
        for part, fs in fields.items():
            if not flag("PARLEY_ALT_GUARD", part):
                continue
            if all(t.get(f) == primary.get(f) for f in fs):
                continue
            probe = {**primary, **{f: t.get(f) for f in fs}}
            if self.cost(sid, probe, v.std) > c0 * (1 + 1e-6) + 1.0:
                return True
        return False

    def loosens_quality(self, sid, v, terms, ref):
        """A sheet with a higher ppm or a lower chargeback than `ref`, unless it is also cheaper for
        us at our estimate of the supplier's floor (the price it ends at, not the one it starts at)."""
        if not ref:
            return False
        looser = False
        for f, worse in (("ppm_commitment", lambda a, b: a > b), ("chargeback_eur_per_complaint", lambda a, b: a < b)):
            a, b = terms.get(f), ref.get(f, v.std.get(f))
            if a is not None and b is not None and worse(a, b):
                looser = True
        if not looser:
            return False
        fl = self.floor_price(sid, v)
        if not fl:
            return True
        base = {**v.std, **ref}
        mine = self.cost(sid, {**terms, "unit_price": fl}, v.std)
        theirs = self.cost(sid, {**self.val.combo_of(base), "unit_price": fl}, v.std)
        return mine >= theirs

    def late_cost_matters(self, c_ref, std_lead=None):
        """The main sheet keeps the lead time we need when being late at the supplier's standard lead
        time would cost more than a small share of the deal: that cost is certain, the supplier's
        saving is a guess. Overtime for a shorter lead time is priced in the package itself."""
        if "lead_time_days" not in self.case["negotiable"]:
            return False
        b = self.buyer
        worst = std_lead if std_lead is not None else max(self.val.options("lead_time_days"))
        late = (b.get("late_cost_per_day") or 0) * max(0, worst - b["need_lead_time_days"])
        return late > LATE_SHARE * c_ref

    def latest_effective_date(self, v):
        """Price increases: the new price should start as late as possible (units before it are
        bought at today's price); we hold that date in the main package."""
        opts = self.case.get("term_options", {}).get("effective_date") or []
        cur = self.buyer.get("current_unit_price")
        sup = supplier_messages(v.events)
        opening = ((sup[0].get("term_sheets") or [{}])[0].get("terms") or {}).get("unit_price") if sup else None
        if "effective_date" not in self.case["negotiable"] or not opts or not cur or not opening or opening <= cur:
            return None
        return max(o for o in opts if o)

    def _payment_probe(self, t, primary):
        """A payment alternative on the other side of the supplier's likely wish: short when we ask
        long, long when we ask short."""
        opts = sorted(self.val.options("payment_days"))
        if primary["payment_days"] >= opts[len(opts) // 2]:
            return t["payment_days"] == opts[0]
        return t["payment_days"] == opts[-1] or t["payment_days"] > primary["payment_days"]

    def chosen_combo(self, v):
        """The structure the supplier picked from our last packages (its standing terms), if any."""
        if not v.asks or not v.sheet_id:
            return None
        standing = self.val.combo_of(v.terms)
        for sheet in v.asks[-1]:
            if self.val.combo_of(sheet["terms"]) == standing:
                return standing
        return None

    def _defer_rank(self, terms):
        d = terms.get("effective_date")
        opts = self.case.get("term_options", {}).get("effective_date") or []
        return opts.index(d) if d in opts else 0

    def fork_cut(self, sid, v):
        """The largest price cut (share of the standing price) of the equal-cost companion sheet, or None.
        An approval threshold: a cut the negotiator can sign alone. A dated price freeze that the
        supplier's own mail confirms: no price cut, all value in the terms. The main sheet is unchanged."""
        f = self.dossier.profiles[sid].fields
        if not f or not v.sheet_id:
            return None
        cuts = []
        t = f.get("approval_threshold_pct")
        if pf_on("fork") and isinstance(t, (int, float)) and t > 0:
            cuts.append(FORK_SHARE * t / 100)
        freeze = f.get("price_freeze")
        if pf_on("freeze") and isinstance(freeze, dict) and still_valid(freeze.get("until")):
            if any(SIGNALS["freeze"].search(m.get("body") or "") for m in supplier_messages(v.events)):
                cuts.append(0.0)
        return min(cuts) if cuts else None

    @staticmethod
    def _differs(t, sheets):
        keys = [k for k in t if k != "unit_price"]
        return all(sum(t[k] != s.get(k) for k in keys) >= 1 for s in sheets)

    # ---- claims -----------------------------------------------------------------------------

    def applicable(self, sid, claim: Claim, v):
        price = self.standing_price(v)
        if claim.kind in self.dead_kinds[sid]:
            return False
        if sid in self.frozen and claim.kind in PRICE_KINDS:
            return False
        if any(r in v.used_refs for r in claim.refs):
            return False
        cur = self.buyer.get("current_unit_price")
        if claim.kind == "should_cost" and claim.figure is not None:
            if claim.figure >= price * 0.98 or (cur and claim.figure > cur * 1.03):
                return False
        if claim.kind in ("alt_quote", "po") and claim.figure is not None and claim.figure >= price * 0.995:
            return False
        if claim.kind == "index":
            series = claim.topic.split(":", 1)[1]
            if series in self.dossier.profiles[sid].hedged_series:
                return False
        return True

    def rival_claim(self, sid, views):
        mine = views[sid]
        best = None
        for other, ov in views.items():
            if other == sid or not ov.sheet_id or not ov.terms.get("unit_price"):
                continue
            if ov.sheet_id in mine.used_refs:
                continue
            p = float(ov.terms["unit_price"])
            if p < self.standing_price(mine) * 0.995 and (best is None or p < best[1]):
                best = (ov.sheet_id, p, other)
        if not best:
            return None
        return Claim(
            "rival",
            [best[0]],
            f"Another bidder's standing offer for this part is EUR {best[1]:.2f} per unit ({best[0]}).",
            9.5,
            best[1],
            "rival",
            sid,
        )

    def pick_claims(self, sid, v, views, first):
        out = []
        rival = self.rival_claim(sid, views) if self.rfq else None
        if rival and first and self.dossier.profiles[sid].impatient and not flag("PARLEY_V1_RFQ", "rival"):
            rival = None
        if rival:
            out.append(rival)
        limit = (FIRST_FACTS + (1 if rival else 0)) if first else (2 if rival else 1)
        if pf_on("facts") and self.dossier.profiles[sid].fields.get("uninformed_owner") is True:
            limit = max(limit, UNINFORMED_FACTS)
        n = self.squeeze(sid, v)
        if n:
            with self.claims_lock:
                left = sum(1 for c in self.claim_bank[sid] if self.applicable(sid, c, v) and not self._reserved(c))
            limit = min(MAX_FACTS, max(limit, math.ceil(left / n)))
        topics = {c.topic for c in out}
        used_topics = set()
        for m in buyer_messages(v.events):
            for c in self.claim_bank[sid]:
                if any(r in (m.get("refs") or []) for r in c.refs):
                    used_topics.add(c.topic)
        with self.claims_lock:
            bank = [c for c in self.claim_bank[sid] if self.applicable(sid, c, v) and not self._reserved(c)]
            if on("SC_GUARD"):
                low = min((self.standing_price(x) for x in views.values() if x.terms.get("unit_price")), default=None)
                if low:
                    bank = [c for c in bank if not (c.kind == "should_cost" and c.figure is not None and c.figure >= low * 0.98)]
            if flag("PARLEY_QUALITY_ONE") and self.case.get("type") == "quality":
                bank = self._one_quality_claim(bank, v)
            picked = self._pick(out, bank, used_topics, topics, limit)
            if first and on("INDEX_R1"):
                extra = self.energy_index(sid, bank, picked)
                if extra:
                    idx = [i for i, c in enumerate(picked) if c.kind == "index"]
                    if idx:
                        picked[idx[0]] = extra
                    else:
                        picked.append(extra)
            for c in picked:
                self.reserved.update(r for r in c.refs if not r.startswith(("offer:", "quote:")))
            return picked

    def energy_index(self, sid, bank, picked):
        """The strongest falling energy or conversion index in the data, if round one does not cite one.
        It takes the place of the other index (one index per mail; that one stays for later)."""
        def energy(c):
            if c.kind != "index" or c.figure is None or c.figure >= 0:
                return False
            series = c.topic.split(":", 1)[1]
            return series in ENERGY_SERIES or bool(ENERGY_NAME.search(self.dossier.series_name(series)))

        if any(energy(c) for c in picked):
            return None
        topics = {c.topic for c in picked}
        cands = [c for c in bank if energy(c) and c.topic not in topics]
        return max(cands, key=lambda c: c.strength) if cands else None

    @staticmethod
    def _one_quality_claim(bank, v):
        """Quality cases: scorecard and quality-notification facts count as one kind; at most one
        per mail, and none once one was cited in this thread."""
        seen = any(r.startswith(QUALITY_PREFIXES) for r in v.used_refs)
        out = []
        for c in bank:
            if quality_claim(c):
                if seen:
                    continue
                seen = True
            out.append(c)
        return out

    def volume_ref(self, v, claims):
        """A forecast ID to back the volume we commit to, if one is still unused in this case."""
        fc = self.dossier._forecast()
        cands = ([fc[0]] if fc else []) + [r for r in self.case.get("refs", []) if r.startswith("fc:")]
        in_claims = {r for c in claims for r in c.refs}
        with self.claims_lock:
            for r in cands:
                if r not in in_claims and r not in v.used_refs and r not in self.reserved:
                    self.reserved.add(r)
                    return r
        return None

    def _reserved(self, c):
        if self.rfq and on("RFQ_SHARE_FACTS"):
            return False
        return any(r in self.reserved for r in c.refs if not r.startswith(("offer:", "quote:")))

    @staticmethod
    def _pick(out, bank, used_topics, topics, limit):
        """Strongest first, one fact per kind in a mail, kinds not yet used in this thread first."""
        kinds = {c.kind for c in out}
        fresh = [c for c in bank if c.topic not in used_topics and c.topic not in topics]
        for c in fresh + [c for c in bank if c not in fresh]:
            if len(out) >= limit:
                break
            if c.topic in topics or c.kind in kinds:
                continue
            out.append(c)
            topics.add(c.topic)
            kinds.add(c.kind)
        return out

    def squeeze(self, sid, v):
        """Rounds left before the cap when time, not the round limit, binds (else None)."""
        n = self.time_rounds.get(sid)
        return n if n is not None and 0 < n < v.moves_left else None

    def has_fresh_fact(self, sid, v):
        with self.claims_lock:
            return any(self.applicable(sid, c, v) and not self._reserved(c) for c in self.claim_bank[sid])

    def learn(self, sid, v: ThreadView):
        """Update what we believe about the supplier from its latest reply."""
        prof = self.dossier.profiles[sid]
        m = self.models[sid]
        r = v.reading
        sup = supplier_messages(v.events)
        last = sup[-1] if sup else {}
        self.dossier.observe(sid, last.get("body") or "")
        if signal("freeze") and (
            FROZEN.search(last.get("body") or "") or (r.flags.get("freeze") and prof.price_freeze)
        ):
            self.frozen.add(sid)
        for s in r.hedged_series:
            if s not in prof.hedged_series:
                prof.hedged_series.append(s)
        if r.flags.get("hedge") and not r.hedged_series:
            for s, w in prof.drivers.items():
                if w >= 0.2 and not s.startswith("LCI") and s not in prof.hedged_series:
                    prof.hedged_series.append(s)
        if r.flags.get("cash") and m.interest_rate < CASH_RATE:
            m.interest_rate = CASH_RATE
        chosen = self.chosen_combo(v)
        if chosen and "payment_days" in chosen and len(v.asks[-1]) > 1:
            asked = [s["terms"].get("payment_days") for s in v.asks[-1]]
            if chosen["payment_days"] == min(asked) < max(asked) and m.interest_rate < CASH_RATE:
                m.interest_rate = CASH_RATE

        prev = [x for x in buyer_messages(v.events)]
        if prev and r.verdicts:
            last_refs = prev[-1].get("refs") or []
            for res in r.verdicts:
                if res == "contradicted":
                    for c in self.claim_bank[sid]:
                        if any(x in last_refs for x in c.refs):
                            self.dead_kinds[sid].add(c.kind)
        for att in last.get("attachments") or []:
            text = att.get("text") or ""
            if "cost" in (att.get("name") or "") and text:
                fc = breakdown_floor(text)
                if fc:
                    self.floor[sid] = fc
                    if not m.full_cost and not flag("PARLEY_REL_NO_BREAKDOWN_COST"):
                        m.full_cost = fc

    # ---- the decision -----------------------------------------------------------------------

    def decide(self, sid, threads) -> Decision:
        d = self._decide(sid, threads)
        if d.kind != "send" or d.lines.get("mirror") or d.lines.get("park"):
            return d
        v = None
        if flag("PARLEY_PI_CAP") and self.case.get("type") == "price_increase":
            v = self.view(sid, threads[sid])
            self._pi_cap(sid, v, d)
        if flag("PARLEY_BUNDLE_PROOF") and self.case.get("type") == "bundling":
            v = v or self.view(sid, threads[sid])
            self._bundle_proof(sid, v, d)
        return d

    def _pi_cap(self, sid, v, d):
        """Price increases: once our first ask was at or below today's price, no later ask goes above
        it; a sheet at or below today's price asks for the earliest effective date."""
        cur = self.buyer.get("current_unit_price")
        if not cur:
            return
        first = v.asks[0][0]["terms"].get("unit_price") if v.asks else None
        cap = first is not None and float(first) <= cur
        opts = [o for o in self.case.get("term_options", {}).get("effective_date") or [] if o]
        early = min(opts) if opts and "effective_date" in self.case["negotiable"] else None
        out = []
        for t in d.sheets:
            t = dict(t)
            if cap and float(t.get("unit_price") or 0) > cur:
                t["unit_price"] = float(cur)
                d.metrics["pi_cap"] = True
            if early and float(t.get("unit_price") or math.inf) <= cur and t.get("effective_date") != early:
                e = {**t, "effective_date": early}
                if self.val.valid_combo(self.val.combo_of(e), v.std) and self.cost(sid, e, v.std) <= self.cost(
                    sid, t, v.std
                ):
                    t = e
            if t not in out:
                out.append(t)
        d.sheets = out

    def _bundle_proof(self, sid, v, d):
        """Bundling: the supplier asks for proof of demand -> cite exactly one forecast or document ID."""
        if not v.reading.flags.get("wants_data"):
            return
        proof = ("fc:", "doc:")
        cited = [r for c in d.claims for r in c.refs if r.startswith(proof)]
        if d.lines.get("volume_ref"):
            if cited:
                d.lines.pop("volume_ref")
            else:
                cited = [d.lines["volume_ref"]]
        if len(cited) > 1:
            keep = cited[0]
            d.claims = [c for c in d.claims if not any(r.startswith(proof) and r != keep for r in c.refs)]
            return
        if cited:
            return
        fc = self.dossier._forecast()
        refs = list(self.case.get("refs", []))
        cands = ([fc[0]] if fc else []) + [r for r in refs if r.startswith(proof)]
        if self.case.get("trigger_doc"):
            cands.append(self.case["trigger_doc"])
        ref = None
        with self.claims_lock:
            for r in cands:
                known = r in refs or self.dossier.fact(r) is not None
                if r.startswith(proof) and known and r not in v.used_refs and r not in self.reserved:
                    ref = r
                    self.reserved.add(r)
                    break
        if not ref:
            return
        if "volume" in d.lines:
            d.lines["volume_ref"] = ref
        else:
            what = "demand forecast" if ref.startswith("fc:") else "planning document"
            d.claims.append(
                Claim("proof", [ref], f"Our {what} backs the volume in this bundle ({ref}).", 4, None, "proof", sid)
            )
        d.metrics["bundle_proof"] = ref

    def _freeze_offer(self, sid, v, last_ask):
        """Outside RFQs: two equal supplier prices in a row -> once, our last price with the longest
        payment (up to 90 days) and the longest contract instead of another price step."""
        if self.rfq or sid in self.freeze_offered or len(v.offers) < 2:
            return None
        a, b = (t.get("unit_price") for t, _ in v.offers[-2:])
        if a is None or b is None or abs(float(a) - float(b)) > 1e-9:
            return None
        if v.reading.flags.get("cash") or self.dossier.profiles[sid].cash_short:
            return None
        fields = self.val.non_price_fields()
        combo = dict(self.val.combo_of(v.terms))
        if "payment_days" in fields:
            pays = [o for o in self.val.options("payment_days") if o is not None and o <= 90]
            if pays:
                combo["payment_days"] = max(pays)
        if "contract_months" in fields:
            months = [o for o in self.val.options("contract_months") if o]
            if months:
                combo["contract_months"] = max(months)
        if combo == self.val.combo_of(v.terms) or not self.val.valid_combo(combo, v.std):
            return None
        sheet = self.val.sheet(combo, float(last_ask["unit_price"]))
        if self.cost(sid, sheet, v.std) >= v.c_standing * (1 - ACCEPT_GAP):
            return None
        self.freeze_offered.add(sid)
        return sheet

    def _decide(self, sid, threads) -> Decision:
        for s, ev in threads.items():
            self.model(s, ev)
        views = {s: self.view(s, ev) for s, ev in threads.items()}
        if not (self.rfq and on("RFQ_SHARE_FACTS")):
            case_used = set().union(*(x.used_refs for x in views.values()))
            for x in views.values():
                x.used_refs |= {r for r in case_used if not r.startswith(("offer:", "quote:"))}
        v = views[sid]
        self.learn(sid, v)
        c_alt = self.val.c_alt
        metrics = {
            "c_standing": _r(v.c_standing),
            "c_alt": _r(c_alt),
            "c_open": _r(self.c_open(views)),
            "status": v.status,
            "moves_left": v.moves_left,
            "flags": sorted(v.reading.flags),
            "conceded": len(v.offers) >= 2
            and v.offers[-1][1] < v.offers[-2][1] * (1 - 1e-4)
            and (v.offers[-1][0].get("unit_price") or 0) <= (v.offers[-2][0].get("unit_price") or 0),
        }
        if self.rfq:
            rivals = [x.c_standing for s, x in views.items() if s != sid and x.alive and self.acceptable(x)]
            metrics["rfq_rank"] = "best" if not rivals or v.c_standing <= min(rivals) else "dearer"

        if not v.alive:
            return Decision("done", sid, reason=f"thread ended ({v.status})", metrics=metrics)
        if not self.val.keeps_must_haves(v.terms, v.std):
            metrics["must_haves"] = "standing offer breaks a must-have"

        mirror = self._mirror(v)
        if v.status == "agreed":
            return Decision(
                "done", sid, reason="supplier agreed to our terms", metrics=metrics, term_sheet_id=v.sheet_id
            )
        if v.status == "final_offer":
            if mirror and v.moves_left > 0 and not self._mirrored_before(v, mirror[1]):
                return Decision(
                    "send",
                    sid,
                    sheets=[mirror[1]],
                    lines={"mirror": True},
                    reason=f"final offer; its alternative sheet costs us {mirror[2]:,.0f} less, we take it as offered",
                    metrics=metrics,
                )
            return Decision("done", sid, reason="final offer: we stop pushing", metrics=metrics)
        if v.moves_left <= 0:
            return Decision("done", sid, reason="round limit reached", metrics=metrics)
        n_time = self.time_rounds.get(sid)
        if n_time is not None:
            metrics["time_rounds"] = n_time
            if n_time <= 0:
                return Decision("done", sid, reason="no time for another round before the cap: close on the standing offer",
                                metrics=metrics)
        squeeze = self.squeeze(sid, v)
        if self.rfq and v.rounds >= 1 and self.out_of_race(sid, v, views):
            if sid not in self.parked and v.moves_left > 0 and self.val.keeps_must_haves(v.terms, v.std):
                self.parked.add(sid)
                return Decision(
                    "send",
                    sid,
                    sheets=[self.val.snap_terms(dict(v.terms))],
                    lines={"park": True},
                    metrics=metrics,
                    reason=f"more than {RFQ_DROP:.0%} dearer than the best bid: its own sheet back once, so the thread stays a backup",
                )
            self.parked.add(sid)
            return Decision(
                "done",
                sid,
                metrics=metrics,
                reason=f"more than {RFQ_DROP:.0%} dearer than the best bid: parked, so no message of ours blocks the session",
            )

        m = self.models[sid]
        cmin = flag("PARLEY_WALK_CMIN") and self.val.relationship and bool(m.full_cost)
        hi = 10 * max(1.0, self.standing_price(v))
        if cmin and v.rounds >= 1 and self.acceptable(v, self.c_open(views)):
            p_min = self.val.cheapest_price(self.val.combo_of(v.terms), v.std, m, hi)
            if self.standing_price(v) <= p_min + 0.005:
                metrics["c_min_price"] = round(p_min, 2)
                return Decision(
                    "done", sid, metrics=metrics,
                    reason=f"the offer is at or below our cost minimum ({p_min:.2f}): a lower price only raises C",
                )

        if c_alt < math.inf and v.rounds >= 2 and self.c_vs_alt(v) > c_alt:
            fl = min(self.floor_price(sid, v) or math.inf, self.standing_price(v) * 0.85)
            cost = (lambda t: self.val.cost(t, v.std)) if flag("PARLEY_REL_PLAIN_ACCEPT") else (lambda t: self.cost(sid, t, v.std))

            def c_at(c):
                p = max(fl, self.val.cheapest_price(c, v.std, m, hi)) if cmin else fl
                return cost(self.val.sheet(c, p))

            c_floor = min(c_at(c) for c in self.candidate_combos(sid, v))
            if c_floor > c_alt and not self.rfq:
                return Decision(
                    "walk",
                    sid,
                    reason=f"even near its floor ({fl:.2f}) C={c_floor:,.0f} is above our alternative {c_alt:,.0f}",
                    metrics=metrics,
                )

        first = v.rounds == 0
        commit = False
        v_stand = self.value(sid, v.terms)
        guard = not first and self.zero_guard(sid, v, views)
        if guard:
            metrics["zero_guard"] = round(self.est_score(sid, v, views), 3)
        if first:
            ap = self.anchor_price(sid, v)
            target_value = self.value(sid, {**v.terms, "unit_price": ap})
            reason = (
                f"anchor at {ap:.2f} on the standing structure ({(1 - ap / self.standing_price(v)) * 100:.1f}% below)"
            )
        else:
            last_ask = v.asks[-1][0]["terms"]
            last_val = self.value(sid, last_ask)
            gap = v_stand - last_val
            prev_gap = None
            if len(v.offers) >= 2:
                prev_gap = self.value(sid, v.offers[-2][0]) - last_val

            if sid in self.frozen:
                lever = self.unused_lever(sid, v, v_stand)
                if lever:
                    field, sheet = lever
                    return Decision(
                        "send",
                        sid,
                        sheets=[sheet],
                        lines=self._lines(sid, v, False, sheet),
                        reason=f"prices are frozen: we trade the {field.replace('_', ' ')} instead",
                        metrics=metrics,
                    )
            b, why = self.beta(sid, v, prev_gap, gap, self.has_fresh_fact(sid, v))

            if self.rfq and self.rival_claim(sid, views):
                b, why = min(b, 0.2), why + "; a cheaper rival does the work"
            base_target = last_val + max(0.0, gap) * b
            if squeeze:
                floor_b = CLOSING_STEP if squeeze == 1 else 1.0 / (squeeze + 1)
                sup_step = self.value(sid, v.offers[-2][0]) - v_stand if len(v.offers) >= 2 else 0.0
                big_move = bool(prev_gap and sup_step > BIG_STEP * prev_gap)
                keep_small = squeeze >= 2 and big_move and rules.env("PARLEY_PACE_BIGMOVE_HOLD") == "1"
                if b < floor_b and not v.reading.flags.get("step_announced") and not keep_small:
                    b, why = floor_b, why + f"; only {squeeze} round(s) left before the cap: a bigger step"
            target_value = last_val + max(0.0, gap) * b
            if squeeze and sup_step > 0 and target_value > base_target:
                target_value = max(base_target, min(target_value, v_stand - sup_step))
            reason = f"move {b:.0%} of the gap ({why})"
            open_value = self.value(sid, v.offers[0][0])
            if on("W_FLOOR") and v.rounds >= 2 and b > 0 and target_value - last_val < W_FLOOR * open_value:
                target_value = last_val + min(max(0.0, gap) * 0.5, W_FLOOR * open_value)
                reason += f"; at least {W_FLOOR:.2%} of the opening (smaller moves draw a final offer)"
            if on("TAPER") and v.rounds >= 2 and target_value - last_val > TAPER_STEP * open_value:
                target_value = last_val + TAPER_STEP * open_value
                reason += f"; tapered to a fixed step of {TAPER_STEP:.1%} of the opening"
            step = self.value(sid, v.offers[-2][0]) - v_stand if len(v.offers) >= 2 else 0.0
            still_moving = step >= MOVING_STEP * open_value and v.moves_left >= 2
            holds = self._holds(v)
            if gap <= v_stand * ACCEPT_GAP and not still_moving and holds >= 1 and not guard:
                first_lever = self.lever_first(sid, v, v_stand, metrics)
                if first_lever:
                    return first_lever
                return Decision(
                    "done",
                    sid,
                    reason=f"gap to the standing offer below {ACCEPT_GAP:.2%} and the supplier stopped moving",
                    metrics=metrics,
                )
            if self.rfq and self.clear_winner(sid, v, views) and gap <= RFQ_TAKE_GAP * v_stand and not guard:
                first_lever = self.lever_first(sid, v, v_stand, metrics)
                if first_lever:
                    return first_lever
                return Decision(
                    "done",
                    sid,
                    metrics={**metrics, "take": True},
                    reason=f"clearly the best bid and within {RFQ_TAKE_GAP:.1%} of our ask: take it before it walks",
                )
            cap = v_stand - KEEP_GAP * open_value
            if target_value > cap:
                target_value = max(last_val, cap)
                reason += f"; capped {KEEP_GAP:.1%} of the opening below its offer until a final offer"
            repeated = v.asks and len(v.asks) >= 2 and self.value(sid, v.asks[-2][0]["terms"]) >= last_val - 1e-6
            if target_value <= last_val + 1e-6 and repeated:
                target_value = min(cap, last_val + MIN_STEP * max(0.0, gap)) if cap > last_val else target_value
                reason += "; never the same ask twice in a row"
            if flag("PARLEY_FREEZE_NARROW"):
                sheet = self._freeze_offer(sid, v, last_ask)
                if sheet:
                    return Decision(
                        "send",
                        sid,
                        sheets=[sheet],
                        lines=self._lines(sid, v, False, sheet),
                        reason="two equal prices in a row: once the longest payment and contract at our price",
                        metrics=metrics,
                    )
            if on("COMMIT_LEVER") and self.commit_lever(sid, v):
                commit = True
                target_value = last_val
                reason = "the supplier says it is at its floor: we offer a firm call-off schedule instead of a price step"
            if (
                self._stuck(v)
                or (self._holds(v) >= 2 and not self.has_fresh_fact(sid, v))
                or (pf_on("hold") and self.early_holder(sid, v))
                or (guard and holds >= 1 and not self.has_fresh_fact(sid, v))
            ):
                lever = self.unused_lever(sid, v, v_stand)
                if lever:
                    field, sheet = lever
                    return Decision(
                        "send",
                        sid,
                        sheets=[sheet],
                        lines=self._lines(sid, v, False, sheet),
                        reason=f"the supplier holds: we offer the {field.replace('_', ' ')} it signals it values",
                        metrics=metrics,
                    )
            if self._stuck(v) and not guard and not commit:
                first_lever = self.lever_first(sid, v, v_stand, metrics)
                if first_lever:
                    return first_lever
                return Decision(
                    "done",
                    sid,
                    reason="supplier held twice with nothing new to give: take the standing offer",
                    metrics=metrics,
                )

        sheets = self.packages(sid, v, target_value, n_alt=1 if not first else 2)
        if not sheets:
            return Decision("done", sid, reason="no valid package to propose", metrics=metrics)
        if v.alive and mirror and self.cost(sid, mirror[1], v.std) <= self.cost(sid, sheets[0], v.std):
            return Decision(
                "send",
                sid,
                sheets=[mirror[1]],
                lines={"mirror": True},
                reason="the supplier's alternative sheet beats our next ask: we take it as offered",
                metrics=metrics,
            )
        if (
            v.c_standing <= self.cost(sid, sheets[0], v.std) * (1 + ACCEPT_GAP)
            and not first
            and self._holds(v) >= 2
            and not guard
            and not commit
        ):
            first_lever = self.lever_first(sid, v, v_stand, metrics)
            if first_lever:
                return first_lever
            return Decision("done", sid, reason="the standing offer is as good as our next ask", metrics=metrics)
        claims = self.pick_claims(sid, v, views, first)
        lines = self._lines(sid, v, first, sheets[0])
        if squeeze == 1 and not first:
            lines["closing"] = True
            sheets = sheets[:1]
        if commit:
            lines["commit"] = True
            self.commit_offered.add(sid)
        fork = self.forks.get(sid)
        if fork and fork[0] in sheets:
            lines["fork"] = (sheets.index(fork[0]) + 1, round(100 * fork[1], 1))
            metrics["fork"] = lines["fork"]
        if "volume" in lines and len(claims) < 3:
            ref = self.volume_ref(v, claims)
            if ref:
                lines["volume_ref"] = ref
        metrics["asks"] = [
            {"terms": s, "C": _r(self.cost(sid, s, v.std)), "value": round(self.value(sid, s), 3)} for s in sheets
        ]
        metrics["supplier_value_standing"] = round(v_stand, 3)
        return Decision("send", sid, sheets=sheets, claims=claims, lines=lines, reason=reason, metrics=metrics)

    def _mirror(self, v):
        if not v.alternatives:
            return None
        best = min(v.alternatives, key=lambda a: a[2])
        if best[2] < v.c_standing * (1 - 1e-4) and self.val.keeps_must_haves(best[1], v.std):
            full = self.val.snap_terms({**v.terms, **best[1]})
            return best[0], full, v.c_standing - best[2]
        return None

    def _mirrored_before(self, v, terms):
        return any(len(a) == 1 and a[0]["terms"] == terms for a in v.asks)

    def out_of_race(self, sid, v, views):
        others = [x.c_standing for s, x in views.items() if s != sid and x.alive and self.acceptable(x)]
        return bool(others) and v.c_standing > min(others) * (1 + RFQ_DROP)

    def clear_winner(self, sid, v, views):
        """This bidder's standing offer beats every other bidder's by more than RFQ_LEAD in our cost."""
        others = [x.c_standing for s, x in views.items() if s != sid and x.alive and self.acceptable(x)]
        if not self.acceptable(v):
            return False
        return not others or min(others) > v.c_standing * (1 + RFQ_LEAD)

    def at_floor(self, v):
        """The supplier held and says this is as low as it goes."""
        last = (supplier_messages(v.events) or [{}])[-1]
        return self._holds(v) >= 1 and bool(FLOOR_TALK.search(last.get("body") or "") or v.reading.flags.get("final_intent"))

    def commit_supported(self, sid):
        """A firm call-off schedule needs something to stand on: a negotiable volume or term, or a
        demand forecast in the data. It has no priced field, so it moves wording only, not C."""
        neg = self.case.get("negotiable") or []
        if "volume_per_year" in neg or "contract_months" in neg:
            return True
        if any(str(r).startswith("fc:") for r in self.case.get("refs", [])):
            return True
        return bool(self.dossier._forecast()) or any(c.kind == "forecast" for c in self.claim_bank.get(sid, []))

    def commit_lever(self, sid, v):
        return sid not in self.commit_offered and v.moves_left >= 2 and self.at_floor(v) and self.commit_supported(sid)

    def unused_lever(self, sid, v, v_stand):
        """A term the supplier has signalled it values and we have not offered yet, priced a little
        below its standing offer: cash -> faster payment, capacity -> lead-time slack, risk -> a
        longer term or an index clause."""
        m, prof, r = self.models[sid], self.dossier.profiles[sid], v.reading
        fields = self.val.non_price_fields()
        asked = {(f, _key(s["terms"].get(f))) for sheets in v.asks for s in sheets for f in fields}
        wants = []
        if "payment_days" in fields and (
            r.flags.get("cash") or r.flags.get("lever_payment") or prof.cash_short or m.interest_rate >= 0.15
        ):
            wants.append(("payment_days", min(self.val.options("payment_days"))))
        if "lead_time_days" in fields and (
            r.flags.get("overtime") or r.flags.get("lever_lead_time") or prof.capacity_tight
        ):
            ok = [
                o
                for o in self.val.options("lead_time_days")
                if self.val.valid_combo({**self.val.combo_of(v.terms), "lead_time_days": o}, v.std)
            ]
            if ok:
                wants.append(("lead_time_days", max(ok)))
        index_value = flag("PARLEY_INDEX_VALUE")
        if "index_clause" in fields and index_value:
            wider = self.wider_deadband(v)
            if wider:
                wants.insert(0, ("index_clause", wider))
        if "contract_months" in fields:
            wants.append(("contract_months", max(self.val.options("contract_months"))))
        hedged = not flag("PARLEY_HEDGE_NOCLAUSE") and (r.flags.get("hedge") or prof.hedged_series)
        if "index_clause" in fields and (
            hedged or r.flags.get("lever_index_clause") or self.case.get("type") == "raw_material"
        ):
            clauses = [o for o in self.val.options("index_clause") if o]
            if clauses and index_value:
                cheapest = min(clauses, key=lambda o: self.clause_cost({**v.terms, "index_clause": o}, v.std))
                wants.append(("index_clause", cheapest))
            elif clauses:
                wants.append(("index_clause", clauses[-1]))
        for fname, value in wants:
            if (fname, _key(value)) in asked or v.terms.get(fname) == value:
                continue
            combo = {**self.val.combo_of(v.terms), fname: value}
            if not self.val.valid_combo(combo, v.std):
                continue
            target = v_stand * (1 - LEVER_ASK)
            price = self.val.price_for_value(combo, target, m)
            sheet = self.val.sheet(combo, price)
            if self.cost(sid, sheet, v.std) < v.c_standing * (1 - ACCEPT_GAP):
                return fname, sheet
        return None

    def wider_deadband(self, v):
        """On a rising series: the clause with the widest deadband on the series the standing offer is
        indexed to, if that is wider than the standing one."""
        cl = v.terms.get("index_clause")
        if not isinstance(cl, dict) or self.val.buyer.get("index_paths", {}).get(cl.get("series"), 0.0) <= 0:
            return None
        same = [
            o
            for o in self.val.options("index_clause")
            if isinstance(o, dict)
            and o.get("series") == cl.get("series")
            and o.get("material_share") == cl.get("material_share")
        ]
        best = max(same, key=lambda o: o.get("deadband_pct") or 0, default=None)
        if best and (best.get("deadband_pct") or 0) > (cl.get("deadband_pct") or 0):
            return best
        return None

    def lever_first(self, sid, v, v_stand, metrics):
        """PARLEY_INDEX_VALUE: before we settle on an indexed offer, ask once for the wider deadband."""
        if not flag("PARLEY_INDEX_VALUE") or v.moves_left < 2 or not self.wider_deadband(v):
            return None
        lever = self.unused_lever(sid, v, v_stand)
        if not lever or lever[0] != "index_clause":
            return None
        return Decision(
            "send",
            sid,
            sheets=[lever[1]],
            lines=self._lines(sid, v, False, lever[1]),
            reason="the index rises: before we settle we ask for the wider deadband",
            metrics=metrics,
        )

    def _holds(self, v):
        """How many of the supplier's last replies did not move its offer (in our cost)."""
        n = 0
        for a, b in zip(reversed(v.offers[:-1]), reversed(v.offers[1:]), strict=False):
            if abs(a[1] - b[1]) <= 1e-4 * max(1.0, a[1]):
                n += 1
            else:
                break
        return n

    def mover(self, v):
        """The supplier cut its offer in each of its last two replies without raising the price, and has
        not signalled a freeze or an internal step."""
        if len(v.offers) < 3 or v.reading.flags.get("freeze") or v.reading.flags.get("step_announced"):
            return False
        (t0, c0), (t1, c1), (t2, c2) = v.offers[-3:]
        prices = [float(t.get("unit_price") or 0) for t in (t0, t1, t2)]
        return c2 < c1 * (1 - 1e-4) and c1 < c0 * (1 - 1e-4) and prices[2] <= prices[1] <= prices[0]

    def early_holder(self, sid, v):
        """Replies 1-2 of the supplier: its latest one held, and a freeze or approval limit is corroborated
        by its own wording or by its documents."""
        if not 2 <= len(v.offers) <= 3 or self._holds(v) < 1:
            return False
        prof = self.dossier.profiles[sid]
        f = prof.fields or {}
        documented = prof.price_freeze or bool(f.get("price_freeze")) or bool(f.get("approval_threshold_pct"))
        worded = any(SIGNALS["freeze"].search(m.get("body") or "") for m in supplier_messages(v.events))
        return documented or worded

    def _stuck(self, v):
        if len(v.offers) < 3:
            return False
        a, b, c = (o[1] for o in v.offers[-3:])
        held = abs(a - b) < 1e-6 and abs(b - c) < 1e-6
        return held

    def _lines(self, sid, v, first, sheet):
        r, prof = v.reading, self.dossier.profiles[sid]
        out = {}
        if first:
            out["breakdown"] = True
        if first or r.flags.get("volume") or r.flags.get("wants_data"):
            t = self.val.breakdown_terms(sheet, v.std)
            months = t.get("contract_months") or round(12 * self.buyer["horizon_years"])
            out["volume"] = (int(t.get("volume_per_year") or self.buyer["need_per_year"]), int(months))
        if (first and prof.we_pay_late_days > 3) or r.flags.get("late_payment"):
            out["pay_on_time"] = True
        pay = sheet.get("payment_days")
        if (
            r.flags.get("cash")
            and pay is not None
            and pay < (v.terms.get("payment_days") or v.std.get("payment_days") or 999)
        ):
            out["cash"] = True
        if not first and not r.flags.get("step_announced") and v.rounds >= 2:
            out["ask_floor"] = True
        return out

    # ---- closing ----------------------------------------------------------------------------

    def close(self, threads, reason="", time_left=False):
        """The case decision when no thread negotiates any more: the cheapest acceptable standing
        offer, else walk away. PARLEY_SAFE_CLOSE: "hold" instead while time is left and no live
        thread got past round 0 (a final or agreed offer still closes)."""
        if time_left and flag("PARLEY_SAFE_CLOSE") and self.round_zero(threads):
            live = [s for s, ev in threads.items() if self.view(s, ev).alive]
            return Decision("hold", live[0], reason=f"{reason}; round 0 with time left: send the anchor first",
                            metrics={"live": live})
        views = {s: self.view(s, ev) for s, ev in threads.items()}
        best = self.best_standing(views)
        c_alt = self.val.c_alt
        if best:
            return Decision(
                "accept",
                best.sid,
                term_sheet_id=best.sheet_id,
                reason=f"{reason}; accept the cheapest standing offer C={best.c_standing:,.0f} (alternative {_r(c_alt)})",
                metrics={"c_deal": _r(best.c_standing), "c_alt": _r(c_alt), "c_open": _r(self.c_open(views))},
            )
        live = [s for s, vv in views.items() if vv.status not in ENDED]
        return Decision(
            "walk",
            live[0] if live else next(iter(views)),
            reason=f"{reason}; no standing offer beats the alternative ({_r(c_alt)}) and keeps the must-haves",
        )

    def round_zero(self, threads):
        """Some thread is alive, none has a final or agreed offer, and we have not countered anywhere."""
        views = [self.view(s, ev) for s, ev in threads.items()]
        live = [v for v in views if v.alive]
        return bool(live) and all(v.status == "open" and v.rounds == 0 for v in live)

    def anchor_decision(self, sid, threads):
        """The plain first ask when decide() fails: the standing structure at our anchor price."""
        for s, ev in threads.items():
            self.model(s, ev)
        v = self.view(sid, threads[sid])
        try:
            ap = self.anchor_price(sid, v)
        except Exception:  # noqa: BLE001
            ap = self.standing_price(v) * (1 - DEPTH.get(self.goal, 0.12))
        sheet = self.val.snap_terms({**v.terms, "unit_price": ap})
        return Decision("send", sid, sheets=[sheet], lines={"breakdown": True},
                        reason=f"decision failed: anchor at {ap:.2f} on the standing structure",
                        metrics={"fallback": "anchor", "asks": [{"terms": sheet, "C": _r(self.cost(sid, sheet, v.std))}]})

    def take_now(self, sid, threads, d):
        """G2, RFQ: this bidder met the take rule or agreed to our terms, and its offer is the cheapest
        acceptable one: close the case now instead of waiting for the other bidders."""
        if not (d.metrics.get("take") or d.metrics.get("status") == "agreed"):
            return False
        best = self.best_standing({s: self.view(s, ev) for s, ev in threads.items()})
        return bool(best and best.sid == sid)

    def final_is_best(self, threads):
        """The cheapest acceptable offer is a final (or agreed) one and no live rival stands cheaper:
        a final offer is withdrawn if it waits for a minute, so close now. PARLEY_ROUND_GUARD: the same
        for a best offer whose thread has no message left (one more timeout ends that thread)."""
        views = {s: self.view(s, ev) for s, ev in threads.items()}
        best = self.best_standing(views)
        if not best:
            return False
        out_of_rounds = flag("PARLEY_ROUND_GUARD") and best.alive and best.moves_left <= 0
        if not (best.status in ("final_offer", "agreed") or out_of_rounds):
            return False
        waited = self.clock() - (self.replied_at.get(best.sid) or self.clock())
        unanswered = [x for s, x in views.items() if s != best.sid and x.alive and len(x.offers) < 2]
        return not unanswered or waited >= FINAL_WAIT_S

    def stale_best(self, threads, done):
        """RFQ: the best offer is a final one that is getting old while others still talk."""
        views = {s: self.view(s, ev) for s, ev in threads.items()}
        best = self.best_standing(views)
        if not best or best.sid not in done or best.status not in ("final_offer", "agreed"):
            return False
        at = self.replied_at.get(best.sid)
        return bool(at and self.clock() - at > STALE_FINAL_S)


def implied_rate(events, d_std):
    """PARLEY_PAY_RATE: the supplier's cost of money from two sheets in its latest mail that differ only
    in payment days and price (equal value to it: p_L(1 - r(d_L - d0)/360) = p_S(1 - r(d_S - d0)/360)),
    clamped to 0.03..0.25; None without such a pair."""
    if d_std is None:
        return None
    for msg in reversed(supplier_messages(events)):
        sheets = [s.get("terms") or {} for s in msg.get("term_sheets") or []]
        for i, a in enumerate(sheets):
            for b in sheets[i + 1:]:
                da, db = a.get("payment_days"), b.get("payment_days")
                pa, pb = a.get("unit_price"), b.get("unit_price")
                if None in (da, db, pa, pb) or da == db:
                    continue
                if any(a.get(k) != b.get(k) for k in set(a) | set(b) if k not in ("payment_days", "unit_price")):
                    continue
                (pl, dl), (ps, ds) = sorted([(float(pa), da), (float(pb), db)], key=lambda x: -x[1])
                den = pl * (dl - d_std) - ps * (ds - d_std)
                if den <= 0 or pl <= ps:
                    continue
                return max(0.03, min(0.25, 360 * (pl - ps) / den))
    return None


def breakdown_floor(text):
    """A supplier's cost floor from its cost breakdown XLSX: every element but overhead and profit,
    plus a third of those."""
    total, margin = 0.0, 0.0
    for line in text.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            continue
        try:
            eur = float(parts[1])
        except ValueError:
            continue
        if re.search(r"profit|margin", parts[0], re.I):
            margin += eur
        else:
            total += eur
    if total <= 0:
        return None
    return round(total + margin / 3, 2)


def _key(value):
    return json.dumps(value, sort_keys=True, default=str)


def _r(x):
    return None if x is None or x == math.inf else round(x, 2)


def quote_standard(text):
    return quote_terms(text).get("standard", {})
