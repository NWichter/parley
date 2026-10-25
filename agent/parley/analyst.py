"""Read a supplier reply: what moved, what it signals, what it asks. Rules first; an optional model
adds a classification of tone and tactic but never a number."""

import json
import re
from dataclasses import asdict, dataclass, field

SIGNALS = {
    "cash": re.compile(
        r"faster payment|cash ?flow|liquidity|helps? us out|pay(ment)? (helps|matters)|"
        r"(14|ten|10)[- ]day payment[^.]*help|shorter payment",
        re.I,
    ),
    "volume": re.compile(r"binding (higher )?volume|which volume can you commit|volume commitment|commit to", re.I),
    "overtime": re.compile(r"overtime", re.I),
    "hedge": re.compile(r"covered at fixed prices|hedged|not our reality", re.I),
    "freeze": re.compile(
        r"frozen|price freeze|price list|preisliste|cannot (touch|change) (our|the) (list )?price", re.I
    ),
    "step_announced": re.compile(
        r"clarify (this )?internally|come back to you|check (this )?with (our )?(management|boss|team)|"
        r"need (management |internal )?approval|discuss (this )?internally|take (this|it) to",
        re.I,
    ),
    "wants_data": re.compile(
        r"which figures|send us the data|data reference|provide the (data|reference)|"
        r"send us the competing offer|proof of|provide (us with )?(the )?(proof|evidence|figures)",
        re.I,
    ),
    "too_low": re.compile(
        r"far below|not a (serious )?basis|unrealistic|cannot take (this|that) seriously|insult", re.I
    ),
    "wants_move": re.compile(r"did not move|no movement|without movement|neither do we|nothing new", re.I),
    "no_new_data": re.compile(r"no new data|room for further adjustments is (very )?limited", re.I),
    "breakdown_refused": re.compile(r"calculation stays internal|do not share", re.I),
    "late_payment": re.compile(r"pay on time|overdue|late payment|actually pay", re.I),
    "near_end": re.compile(
        r"very close to|almost there|close the gap|wrap (this|it) up|let's agree|lock this in", re.I
    ),
}
CASH_STRICT = re.compile(
    r"cash ?flow|liquidity|short of cash|tight on cash|cash (?:need|position|is tight)|needs? (?:the )?cash|"
    r"most now is cash|\bis cash\b|short-time work|kurzarbeit|\bbanks?\b(?! holiday)|credit (?:line|insurer)|"
    r"prepayment|pay(?:ment)? in advance|skonto|cash discount|overdue|pay (?:our )?invoices within|"
    r"(?:faster|earlier|shorter|quicker) payment[^.;]{0,40}\b(?:help|need|matter|important|crucial)|"
    r"pay (?:us )?(?:faster|earlier)[^.;]{0,40}\b(?:help|need|matter|important|crucial)|"
    r"pay(?:ment)? (?:helps|matters)|(?:14|ten|10)[- ]day payment[^.]*help",
    re.I,
)
FAIR_POINT = re.compile(r"did not know that|a fair point|good data work", re.I)
TOOK_INTO_ACCOUNT = re.compile(r"took it into account", re.I)
NO_CHANGE = re.compile(r"does not change our (calculation|position)", re.I)
SERIES_RE = re.compile(r"\b([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+)\b")
PAY_DAYS = re.compile(r"(?:standard )?Zahlungsziel (?:is )?(\d+) days", re.I)


@dataclass
class Reading:
    status: str
    flags: dict = field(default_factory=dict)
    verdicts: list = field(default_factory=list)
    fair_points: int = 0
    accounted: int = 0
    unmoved: int = 0
    series_named: list = field(default_factory=list)
    hedged_series: list = field(default_factory=list)
    alternatives: list = field(default_factory=list)
    attachments: list = field(default_factory=list)
    tone: str = ""
    tactic: str = ""
    source: str = "rules"

    def to_dict(self):
        return asdict(self)


def read_reply(msg) -> Reading:
    body = msg.get("body") or ""
    from .policy import flag

    r = Reading(status=msg.get("status") or "open")
    strict = flag("PARLEY_CASH_STRICT")
    for name, rx in SIGNALS.items():
        if name == "cash" and strict:
            rx = CASH_STRICT
        if rx.search(body):
            r.flags[name] = True
    r.verdicts = [c.get("result") for c in msg.get("claim_results") or []]
    r.fair_points = len(FAIR_POINT.findall(body))
    r.accounted = len(TOOK_INTO_ACCOUNT.findall(body))
    r.unmoved = len(NO_CHANGE.findall(body))
    r.series_named = sorted(set(SERIES_RE.findall(body)))
    for m in re.finditer(r"our ([A-Z][A-Z0-9-]+) is covered", body):
        r.hedged_series.append(m.group(1))
    if r.flags.get("hedge"):
        for s in r.series_named:
            if re.search(re.escape(s) + r"[^.]{0,60}covered", body):
                r.hedged_series.append(s)
    sheets = msg.get("term_sheets") or []
    r.alternatives = [s for s in sheets[1:] if s.get("terms")]
    r.attachments = [a.get("name") for a in msg.get("attachments") or []]
    return r


ANALYST_SYSTEM = """You read one e-mail from an industrial supplier to a buyer and extract signals.
Reply with one JSON object only, every field present:
{"tone": "warm|neutral|irritated|impatient",
 "cash_need": true|false,          the supplier says faster or earlier payment matters to it
 "price_freeze": true|false,       prices are frozen / fixed by a list / no price reductions possible
 "hedged_input": true|false,       an input is covered or bought forward at fixed prices
 "step_announced": true|false,     it will check internally / needs approval / will come back on price
 "wants_data": true|false,         it asks for figures, proof or a data reference
 "walk_threat": true|false,        it signals it may end the talks
 "final_intent": true|false,       it says this is its last word
 "too_low": true|false,            it says the buyer's ask is far from any basis
 "preferred_lever": "payment|lead_time|contract_length|volume|index_clause|none"}
Only report what the e-mail says. No other text."""

SIGNALS_MAP = {
    "cash_need": "cash",
    "price_freeze": "freeze",
    "hedged_input": "hedge",
    "step_announced": "step_announced",
    "wants_data": "wants_data",
    "walk_threat": "walk_threat",
    "final_intent": "final_intent",
    "too_low": "too_low",
}


def classify(llm, msg, timeout=6.0):
    """Model reading of a supplier e-mail: a dict of signals, or None (rules only)."""
    if llm is None:
        return None
    body = (msg.get("body") or "")[:3000]
    out = llm.json(ANALYST_SYSTEM, body, role="analyst", timeout=timeout, max_tokens=500)
    if not isinstance(out, dict):
        return None
    keep = set(SIGNALS_MAP) | {"tone", "preferred_lever"}
    return {k: v for k, v in out.items() if k in keep}


def merge(reading: Reading, extra):
    """Rules OR model: a flag the model reports is added; nothing the rules found is removed."""
    if not extra:
        return reading
    reading.source = "rules+llm"
    reading.tone = str(extra.get("tone") or "")
    for key, flag in SIGNALS_MAP.items():
        if extra.get(key) is True:
            reading.flags[flag] = True
    lever = str(extra.get("preferred_lever") or "none")
    if lever != "none":
        reading.flags[f"lever_{lever}"] = True
    return reading


def standard_payment_days(body):
    m = PAY_DAYS.search(body or "")
    return int(m.group(1)) if m else None


def describe(reading: Reading):
    return json.dumps({k: v for k, v in reading.to_dict().items() if v}, ensure_ascii=False)
