"""Conditional switches: case and supplier attributes known before the first e-mail turn candidate
flags on for one case. An environment variable that is set always wins; PARLEY_RULES=0 turns all rules off.

The overrides are bound to the case's threads (the case thread and the pools it starts), because one
process plays several cases at once."""

import os
import threading

_local = threading.local()

RELATIONSHIP = ("stabilise", "secure_supply")

# (name, flag, value, condition on the case facts).
RULES = [
    # Relationship goal: the first ask was so shallow that the supplier often agreed to it at once.
    ("depth", "PARLEY_DEPTH_ADD", "stabilise:0.03,secure_supply:0.03", lambda f: f["goal"] in RELATIONSHIP),
]


def env(name):
    """The value of a PARLEY_* switch: the environment first, then this case's rule overrides."""
    raw = os.environ.get(name)
    if raw is not None:
        return raw
    return getattr(_local, "overrides", {}).get(name)


def bind(overrides):
    _local.overrides = dict(overrides or {})


def current():
    return dict(getattr(_local, "overrides", {}))


def facts(case, fields):
    """What the rules may look at: case type, bidders, negotiable terms, goal, and the profile fields
    of every supplier in the case (the same mapping for every supplier)."""
    sups = case.get("suppliers") or []
    prof = [((fields.get(s) or {}).get("summary") or {}) for s in sups]
    buyer = case.get("buyer") or {}
    return {
        "type": case.get("type"),
        "bidders": len(sups),
        "negotiable": set(case.get("negotiable") or []),
        "goal": buyer.get("category_goal"),
        "alternative": (buyer.get("alternative") or {}).get("kind"),
        "index_paths": bool(buyer.get("index_paths")),
        "cash_need": any(p.get("cash_need") in ("weak", "strong") for p in prof),
        "freeze": any(p.get("price_freeze") for p in prof),
        "hedged": any(p.get("hedged_inputs") for p in prof),
        "uninformed_owner": any(p.get("uninformed_owner") for p in prof),
        "signers": {p.get("decision_maker") for p in prof},
        "short_pay": any("payment_days_short" in (p.get("preferred_levers") or []) for p in prof),
    }


def derive(case, fields):
    """PARLEY_RULES=0 turns every rule off; PARLEY_RULES=depth,index keeps only those rules."""
    raw = os.environ.get("PARLEY_RULES", "1").strip().lower()
    if raw in ("0", "off", "false", "no", "none"):
        return {}
    keep = None if raw in ("1", "on", "true", "yes", "all") else {x.strip() for x in raw.split(",")}
    f = facts(case, fields)
    out = {}
    for rule, name, value, cond in RULES:
        if (keep is None or rule in keep) and cond(f):
            out[name] = value
    return out
