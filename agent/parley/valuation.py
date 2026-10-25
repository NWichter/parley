"""Price any term sheet for the buyer (the arena's published cost function) and estimate what it is
worth to the supplier. Every number Parley sends or decides on comes from here."""

import itertools
import math
from dataclasses import dataclass, field

from .kit.cost import RELATIONSHIP_GOALS, closing_terms, must_haves_ok, snap, supplier_value, total_cost

CLAUSE_RISK_PREMIUM = 0.04


def must_haves_safe(case, terms, std=None):
    """must_haves_ok, but without the supplier's standard terms (quote PDF missing or unreadable) a
    must-have the sheet and the fixed terms leave open counts as broken: at a deal it closes on a
    standard we do not know, not on our plan."""
    if not std:
        known = set(terms or {}) | set(case.get("fixed_terms") or {})
        if any(m["field"] not in known for m in case["buyer"].get("must_haves", [])):
            return False
    return must_haves_ok(case, terms, std)


@dataclass
class SupplierModel:
    """Our estimate of one supplier: its standard terms, cost of money and full cost per unit."""

    standard: dict = field(default_factory=dict)
    interest_rate: float = 0.08
    full_cost: float | None = None
    real_ppm: float | None = None

    def as_kit(self):
        out = {"standard": self.standard, "interest_rate": self.interest_rate}
        if self.full_cost:
            out["full_cost"] = self.full_cost
        if self.real_ppm is not None:
            out["ppm"] = self.real_ppm
        return out


class Valuation:
    def __init__(self, case):
        self.case = case
        self.buyer = case["buyer"]
        self.relationship = self.buyer.get("category_goal") in RELATIONSHIP_GOALS

    def cost(self, terms, std=None, model: SupplierModel | None = None):
        """C in EUR. `std`: the supplier's standard terms (quote PDF) for fields the sheet leaves out;
        `model`: our estimate of the supplier, for the supplier-health charge."""
        sup = model.as_kit() if (model and model.full_cost and self.relationship) else None
        try:
            return total_cost(self.case, terms, std, sup)
        except (KeyError, TypeError, ZeroDivisionError):
            return total_cost(self.case, terms, std)


    def breakdown_terms(self, terms, std=None):
        return closing_terms(self.case, terms, std)

    def keeps_must_haves(self, terms, std=None):
        return must_haves_safe(self.case, terms, std)

    @property
    def alternative(self):
        alt = self.buyer.get("alternative") or {}
        if alt.get("kind") in (None, "none") or not (alt.get("terms") or {}).get("unit_price"):
            return None
        return alt

    @property
    def c_alt(self):
        alt = self.alternative
        return total_cost(self.case, alt["terms"]) if alt else math.inf

    def supplier_value(self, terms, model: SupplierModel):
        """Cash value per unit to the supplier, plus a rough charge for terms that cost it effort."""
        std = {**model.standard, **self.case.get("fixed_terms", {})}
        full = closing_terms(self.case, terms, std)
        base = {**std}
        for k in ("payment_days", "incoterm", "lead_time_days"):
            base.setdefault(k, full.get(k))
        try:
            value = supplier_value(self.case, terms, {**model.as_kit(), "standard": base})
        except (KeyError, TypeError, ZeroDivisionError):
            value = full.get("unit_price") or 0.0
        price = full.get("unit_price") or 0.0
        std_lead = base.get("lead_time_days")
        lead = full.get("lead_time_days")
        if std_lead and lead is not None:
            if lead < std_lead:
                value -= price * 0.003 * (std_lead - lead)
            elif lead > std_lead:
                value += price * 0.0005 * min(lead - std_lead, 20)
        cur = self.buyer.get("current_unit_price")
        if cur and full.get("effective_date") and self.case.get("term_options", {}).get("effective_date"):
            sold_new = self._new_share(full)
            value += 0.5 * (1 - sold_new) * max(0.0, price - cur)
        ppm_std, ppm = base.get("ppm_commitment"), full.get("ppm_commitment")
        if ppm_std and ppm is not None and ppm < ppm_std:
            value -= price * 0.02 * (1 - ppm / ppm_std)
        value += self.clause_value(full, price)
        return value

    def clause_value(self, t, price):
        """An index clause moves the input risk to us: the supplier gains the expected index move
        and sheds a risk premium on its material share."""
        clause = t.get("index_clause")
        if not clause:
            return 0.0
        path = self.buyer.get("index_paths", {}).get(clause["series"], 0.0)
        moved = max(0.0, abs(path) - clause["deadband_pct"] / 100) * math.copysign(1, path)
        share = clause["material_share"] * self._new_share(t)
        return price * share * (moved + CLAUSE_RISK_PREMIUM)


    def _new_share(self, t):
        from .kit.cost import _units_before, _years

        years = _years(self.case, t)
        sold = t["volume_per_year"] * years
        return 1 - _units_before(self.case, t, years) / sold if sold else 1.0

    # ---- the option grid ------------------------------------------------------------------

    def non_price_fields(self):
        return [f for f in self.case["negotiable"] if f != "unit_price"]

    def options(self, f, model: SupplierModel | None = None):
        opts = list(self.case.get("term_options", {}).get(f) or [])
        if not opts:
            std = (model.standard if model else {}) or {}
            plan = closing_terms(self.case, {"unit_price": 1.0}, std)
            opts = [plan.get(f)]
        return opts

    def combos(self, model: SupplierModel | None = None, limit=4000):
        fields = self.non_price_fields()
        grids = [self.options(f, model) for f in fields]
        out = []
        for values in itertools.product(*grids):
            out.append(dict(zip(fields, values, strict=True)))
            if len(out) >= limit:
                break
        return out

    def snap_terms(self, terms):
        out = {}
        for f in self.case["negotiable"]:
            v = terms.get(f)
            if f == "unit_price":
                out[f] = round(v, 2) if v is not None else None
            else:
                out[f] = snap(self.case, f, v) if v is not None else self.options(f)[0]
        return out

    def sheet(self, combo, price):
        return self.snap_terms({**combo, "unit_price": price})

    # ---- solving for a price ----------------------------------------------------------------

    def price_for_cost(self, combo, target, std=None, model: SupplierModel | None = None, lo=0.0, hi=None):
        """Highest unit price on this combo whose buyer cost stays at or below `target`."""
        hi = hi or 10 * max(1.0, self.reference_price())
        lo = max(lo, self.cheapest_price(combo, std, model, hi))
        c_lo = self.cost({**combo, "unit_price": lo}, std, model)
        if c_lo > target:
            return None
        c_hi = self.cost({**combo, "unit_price": hi}, std, model)
        if c_hi <= target:
            return hi
        for _ in range(48):
            mid = (lo + hi) / 2
            if self.cost({**combo, "unit_price": mid}, std, model) <= target:
                lo = mid
            else:
                hi = mid
        return math.floor(lo * 100 + 1e-9) / 100

    def cheapest_price(self, combo, std, model, hi):
        """The price below which our cost rises again (the supplier-health charge); 0 without one."""
        if not (self.relationship and model and model.full_cost):
            return 0.0
        a, b = model.full_cost * 0.5, hi
        for _ in range(60):
            m1, m2 = a + (b - a) / 3, b - (b - a) / 3
            if self.cost({**combo, "unit_price": m1}, std, model) <= self.cost({**combo, "unit_price": m2}, std, model):
                b = m2
            else:
                a = m1
        return (a + b) / 2

    def price_for_value(self, combo, value, model: SupplierModel):
        """Unit price on this combo that is worth `value` per unit to the supplier."""
        lo, hi = 0.0, 10 * max(1.0, self.reference_price())
        for _ in range(48):
            mid = (lo + hi) / 2
            if self.supplier_value({**combo, "unit_price": mid}, model) < value:
                lo = mid
            else:
                hi = mid
        return math.ceil(hi * 100 - 1e-9) / 100

    def reference_price(self):
        b = self.buyer
        return b.get("current_unit_price") or b.get("fallback_unit_price") or 1.0

    def combo_of(self, terms):
        return {f: terms.get(f) for f in self.non_price_fields()}

    def valid_combo(self, combo, std=None):
        return self.keeps_must_haves({**combo, "unit_price": 1.0}, std)

