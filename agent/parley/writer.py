"""Turn a decision into an e-mail. Code writes every figure, data ID and term sheet; a model may
write the opening paragraph, which must not contain a single digit or ID."""

import re
import zlib

from . import rules

FORBIDDEN = re.compile(
    r"\d|[a-z]+:[\w@-]+|urgent|deadline|budget|no (other )?alternative|desperate|must sign|last chance|"
    r"we need this|asap|immediately|smaller step|bigger step|our step|this round|hold firm|concede|concession|"
    r"earlier|previous|in the past|last time|goodwill|history|we will explain|happy to explain|explain where|"
    r"as you (said|mentioned|told)|you (said|mentioned|told us) (before|earlier)",
    re.I,
)
SIGN_OFF = "Kind regards\nHollerau Pumpenwerke, Purchasing"

WRITER_SYSTEM = """You write the opening paragraph of a buyer's e-mail to an industrial supplier.
The buyer is Hollerau Pumpenwerke, Purchasing, a German pump maker. Write 2 to 3 short sentences in
plain, polite, factual business English that react to what the supplier just wrote (thank them for a
real step, acknowledge a concern they raised, answer a non-numeric question).
Hard rules:
- No digits, no prices, no percentages, no dates, no data IDs. Figures and terms follow below your
  paragraph and are written by someone else.
- Do not promise anything, do not threaten, do not mention urgency, a budget, deadlines or whether we
  have other options.
- Do not repeat the term sheets or arguments. No greeting line and no sign-off.
- Never describe our own tactics (how big our step is, that we hold or concede, what we plan next).
- React only to what the supplier wrote in this e-mail. Never refer to earlier talks, history, goodwill
  or anything the supplier did not write here, and never promise explanations or data.

Reply with the paragraph only."""


def describe_terms(case, terms):
    out = []
    for f in case["negotiable"]:
        v = terms.get(f)
        if f == "unit_price":
            out.insert(0, f"EUR {v:.2f} per unit")
        elif f == "payment_days":
            out.append(f"payment {v} days net")
        elif f == "lead_time_days":
            out.append(f"lead time {v} days")
        elif f == "contract_months":
            out.append(f"{v} months" if v else "a single order")
        elif f == "volume_per_year":
            out.append(f"{v} pieces a year")
        elif f == "effective_date":
            out.append(f"new price from {v}")
        elif f == "incoterm":
            out.append(f"delivery {v}")
        elif f == "index_clause":
            if v:
                out.append(
                    f"index clause on {v['series']} ({v['material_share'] * 100:.0f}% material share, "
                    f"{v['deadband_pct']:g}% deadband, base {v['base_month']})"
                )
            else:
                out.append("fixed price without index clause")
        elif f == "tooling_eur":
            out.append(f"tooling EUR {v:.2f}")
        elif f == "ppm_commitment":
            out.append(f"quality commitment {v} ppm")
        elif f == "chargeback_eur_per_complaint":
            out.append(f"chargeback EUR {v:.2f} per complaint")
    return ", ".join(out)


SIGN_OFFS = re.compile(r"regards|gr(ü|ue|u)(ß|ss)e?|gru(ß|ss)|best|cheers|sincerely|pozdrawiam|pozdravem|thanks|thank you|yours|"
                       r"mit freundlichen|herzliche", re.I)
COMPANY = re.compile(r"\d|GmbH|Sp\.|s\.r\.o|a\.s\.|\bAG\b|\bKG\b|Ltd|Inc\b|Co\.|S\.A\.|S\.p\.A|B\.V\.|Kft|Purchasing|Hollerau", re.I)


def greeting(supplier_msg):
    """'Dear <name>,' from the line after the supplier's sign-off, else a neutral greeting."""
    body = (supplier_msg or {}).get("body") or ""
    lines = [x.strip().strip(",") for x in body.strip().splitlines() if x.strip()]
    for i in range(len(lines) - 1, 0, -1):
        if SIGN_OFFS.search(lines[i - 1]) and len(lines[i - 1].split()) <= 6:
            cand = lines[i]
            words = cand.split()
            if 2 <= len(words) <= 4 and not COMPANY.search(cand) and not SIGN_OFFS.search(cand) and all(w[:1].isupper() for w in words):
                return f"Dear {cand},"
            break
    return "Dear Sir or Madam,"


NEUTRAL_GREETING = "Dear Sir or Madam,"


def flag(name):
    """Prose switches, read per call: PARLEY_<NAME>=0 turns one off."""
    return (rules.env(f"PARLEY_{name}") or "1") != "0"


# Candidate switches that passed the gate: on unless PARLEY_<NAME>=0.
ADOPTED = {"ACK_OPENING": "1"}


def candidate(name):
    """Candidate switches, off unless PARLEY_<NAME>=1 (ADOPTED: on unless =0)."""
    raw = rules.env(f"PARLEY_{name}")
    return (ADOPTED.get(name, "") if raw is None else raw) == "1"


COMMIT_LINE = (
    "Instead of another price step, we can commit to a firm quarterly call-off schedule for the full volume, "
    "backed by our demand forecast, so you can plan capacity and material ahead."
)
MARGIN = re.compile(r"\bmargin\b", re.I)


def acknowledgement(case, reading, supplier_msg, used=()):
    """One sentence that names the concern the supplier raised in this mail (cash or margin), without a
    promise. Thanking a supplier for its cost drivers or a refused breakdown made it hold in practice."""
    flags = reading.flags if reading else {}
    body = (supplier_msg or {}).get("body") or ""
    out = []
    if flags.get("cash"):
        if "payment_days" in (case.get("negotiable") or []):
            out.append("We understand that faster payment matters to you, and we have looked at it in the terms below.")
        else:
            out.append("We understand that faster payment matters to you; payment terms are fixed for this part, so our proposal works on the terms below.")
    if MARGIN.search(body):
        out.append("We understand the pressure on your margin.")
    used = {_norm(u) for u in used}
    fresh = [x for x in out if not any(_norm(x) in u for u in used)]
    return " ".join((fresh or out)[:2])


def greeting_from(history, supplier_msg=None):
    """The contact's name from the newest supplier mail in the thread that signs with one."""
    for m in reversed(history or []):
        g = greeting(m)
        if g != NEUTRAL_GREETING:
            return g
    return greeting(supplier_msg)


OPENINGS = {
    "accept": ["Thank you; we accept your package as offered.", "Thank you; your package works for us as offered."],
    "first_increase": [
        "Thank you for your letter and your quote; we have checked it against our own data and cannot accept the increase as proposed.",
        "Thank you for your quote; our own data does not support the increase at this level.",
    ],
    "first_rfq": [
        "Thank you for your quote; we intend to award this part to one supplier for the full term.",
        "Thank you for your quote; we plan to place the full volume for this part with one supplier.",
    ],
    "first_rfq_dearer": [
        "Thank you for your quote; at this price level we cannot sign it yet.",
        "Thank you for your quote; your price is still above what we can sign for this part.",
    ],
    "first_quality": [
        "Thank you for your quote; we would like to agree the quality terms for this part on a clear and measurable basis.",
        "Thank you for your quote; we propose clear and measurable quality terms for this part below.",
    ],
    "first_other": [
        "Thank you for your quote and for the time your team has put into it.",
        "Thank you for your quote; we have reviewed it and propose the terms below.",
    ],
    "last_round": [
        "Thank you for your reply. We would like to conclude on this part, and the terms below are our proposal for that.",
        "Thank you for your answer. We would like to bring this part to a conclusion on the terms below.",
    ],
    "step_announced": [
        "Thank you for your reply. When you have checked, please send your figures on {pk}.",
        "Thank you for looking into this. When you have checked, please send your figures on {pk}.",
    ],
    "data_request": [
        "Thank you for your questions. The data references are set out below.",
        "Thank you for asking for our sources. The references are given in brackets below.",
    ],
    "rfq_best": [
        "{thanks} We would like to settle the remaining terms for this part with you.",
        "{thanks} We are working towards the award on the terms below.",
    ],
    "rfq_dearer": [
        "{thanks} Your price is still above what we can sign for this part.",
        "{thanks} At this price level we cannot sign yet.",
    ],
    "conceded": [
        "Thank you for the step you have taken; we appreciate it.",
        "Thank you for moving on the terms; this helps.",
        "Thank you for your revised offer.",
    ],
    "revised": ["Thank you for your revised offer.", "Thank you for your updated terms."],
    "hold": [
        "Thank you for your reply.",
        "Thank you for your answer. Our position is based on the data below.",
        "Thank you. We have looked at your answer again.",
    ],
    "spare": [
        "Thank you for your message.",
        "Thank you for getting back to us.",
        "Many thanks for your reply.",
        "Thank you for your e-mail.",
        "Thank you for your response.",
        "Thank you for the update.",
        "Thank you for your note.",
        "Thank you for your answer; our proposal follows below.",
        "Thank you; our proposal follows below.",
        "Thank you for your reply; please see our terms below.",
        "Thank you; please see our proposal below.",
        "Many thanks; our terms follow below.",
    ],
}
LEADINS = [
    "We have set out {k} packages below. Each would be an acceptable basis for agreement for us; if none fits, "
    "please tell us which term is the hardest for you:",
    "Below are {k} packages we could sign. If one term is the obstacle, please name it:",
]
STEP_LINE_OLD = "We look forward to hearing back from you once you have checked internally."
STEP_LINE_NEW = "When you have checked, please send your figures on {pk}."


def _norm(text):
    return " ".join((text or "").split()).lower()


def opening_of(body):
    """The paragraph after the greeting line of one of our mails."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", (body or "").strip()) if p.strip()]
    return paras[1] if len(paras) > 1 else ""


def situation(case, decision, reading, first):
    """Which opening set fits this mail, from what the supplier's last mail and our own data show."""
    m, lines = decision.metrics, decision.lines
    flags = reading.flags if reading else {}
    rank = m.get("rfq_rank")
    if lines.get("mirror"):
        return "accept"
    if first:
        t = case.get("type")
        if t == "rfq":
            return "first_rfq_dearer" if rank == "dearer" else "first_rfq"
        if t in ("price_increase", "raw_material"):
            return "first_increase"
        if t == "quality":
            return "first_quality"
        return "first_other"
    if m.get("moves_left") == 1:
        return "last_round"
    if flags.get("step_announced"):
        return "step_announced"
    if flags.get("wants_data") and (decision.claims or lines.get("volume_ref")):
        return "data_request"
    if rank:
        return "rfq_best" if rank == "best" else "rfq_dearer"
    if m.get("moved"):
        return "conceded" if m.get("conceded") else "revised"
    return "hold"


def situation_opening(case, decision, reading, first, ctx):
    """(opening, situation): the variant picked by our mail count, never one already used in the thread."""
    n = int(ctx.get("n") or 0)
    used = {_norm(o) for o in ctx.get("prev_openings") or []}
    sit = situation(case, decision, reading, first)
    pool = list(OPENINGS[sit])
    if sit == "hold" and not (decision.claims or decision.lines.get("volume_ref")):
        pool = [o for o in pool if "data below" not in o]
    off = (zlib.crc32(str(ctx.get("key") or case.get("case_id") or "").encode()) + n) % len(pool)
    pool = pool[off:] + pool[:off]
    fill = {
        "pk": "one of the packages below" if len(decision.sheets) > 1 else "the package below",
        "thanks": "Thank you for your revised offer." if decision.metrics.get("conceded") else "Thank you for your reply.",
    }
    for cand in pool + OPENINGS["spare"]:
        text = cand.format(**fill)
        if _norm(text) not in used:
            return text, sit
    return OPENINGS["spare"][n % len(OPENINGS["spare"])], sit


def template_opening(case, decision, reading, first):
    if decision.lines.get("park"):
        return "Thank you for your reply. We are comparing all offers for this part and will come back to you shortly."
    if decision.lines.get("mirror"):
        return "Thank you for your reply. We take your alternative package exactly as you offered it."
    if first:
        if case.get("type") in ("price_increase", "raw_material"):
            return "Thank you for your letter and your quote. We have looked at it carefully against our data and cannot follow the increase in this form."
        if case.get("type") == "rfq":
            return "Thank you for your quote. We would like to award this part to one partner for the full term."
        return "Thank you for your quote and for the time your team has put into it."
    if reading and reading.flags.get("step_announced"):
        if flag("LEADIN"):
            pk = "one of the packages below" if len(decision.sheets) > 1 else "the package below"
            return "Thank you for your reply. " + STEP_LINE_NEW.format(pk=pk)
        return "Thank you for your reply. " + STEP_LINE_OLD
    if decision.metrics.get("moved"):
        return "Thank you for your reply and for the step you have taken; we appreciate it."
    return "Thank you for your reply."


def llm_opening(llm, case, decision, supplier_msg, persona, timeout=12.0):
    if llm is None or not supplier_msg:
        return None
    situation = [
        f"Case: {case.get('title', '')}",
    ]
    if decision.lines.get("mirror"):
        situation.append("We accept the supplier's own alternative package as offered.")
    if decision.lines.get("park"):
        situation.append("We are still comparing offers and ask the supplier to keep its offer open; no commitment.")
    user = "\n".join(situation) + "\n\nThe supplier's last e-mail:\n" + (supplier_msg.get("body") or "")[:2500]
    text = llm.text(WRITER_SYSTEM, user, role="writer", timeout=timeout, max_tokens=600)
    if not text:
        return None
    text = text.strip().strip('"')
    if FORBIDDEN.search(text) or len(text.split()) > 90 or len(text) < 20:
        return None
    return text


def compose(case, decision, supplier_msg=None, reading=None, first=False, llm=None, persona=None, ctx=None):
    """ctx (optional): n = our mails so far in the thread, prev_openings, history = the supplier's
    mails in the thread, key = a stable thread key for the variant offset."""
    ctx = ctx or {}
    used_llm = False
    opening = llm_opening(llm, case, decision, supplier_msg, persona)
    if opening:
        used_llm = True
    elif flag("OPENINGS"):
        opening, _sit = situation_opening(case, decision, reading, first, ctx)
    else:
        opening = template_opening(case, decision, reading, first)
    if not used_llm and not first and candidate("ACK_OPENING") and not decision.lines.get("mirror"):
        ack = acknowledgement(case, reading, supplier_msg, ctx.get("prev_openings") or ())
        if ack:
            opening = f"{opening} {ack}"
    hello = greeting_from(ctx.get("history"), supplier_msg) if flag("GREETING") else greeting(supplier_msg)
    parts = [hello, "", opening, ""]
    lines = decision.lines
    if "volume" in lines and not lines.get("mirror"):
        vol, months = lines["volume"]
        parts.append(
            f"We commit to a binding volume of {vol} pieces a year over {months} months, our full need for this part"
            + (
                (
                    f", as in our demand forecast ({lines['volume_ref']})."
                    if lines["volume_ref"].startswith("fc:")
                    else f", as in our planning ({lines['volume_ref']})."
                )
                if lines.get("volume_ref")
                else "."
            )
        )
    for c in decision.claims:
        parts.append(c.sentence)
    if lines.get("pay_on_time"):
        parts.append("We will pay on the agreed due date; our accounts payable team has this in hand.")
    if lines.get("cash") and not lines.get("mirror"):
        parts.append("The packages below include faster payment.")
    if lines.get("commit") and not lines.get("mirror"):
        parts.append(COMMIT_LINE)
    if decision.claims or "volume" in lines or lines.get("pay_on_time") or lines.get("commit"):
        parts.append("")
    sheets = decision.sheets
    if len(sheets) == 1:
        lead = "We confirm" if lines.get("mirror") else "We note your current offer" if lines.get("park") else "On this basis we propose"
        parts.append(f"{lead}: {describe_terms(case, sheets[0])}.")
    elif sheets:
        if flag("LEADIN"):
            parts.append(LEADINS[int(ctx.get("n") or 0) % len(LEADINS)].format(k=len(sheets)))
        else:
            parts.append(f"Here are {len(sheets)} packages that would work for us; please tell us which one suits you:")
        for i, s in enumerate(sheets, 1):
            parts.append(f"{i}. {describe_terms(case, s)}")
        if lines.get("fork"):
            k, pct = lines["fork"]
            if pct <= 0:
                parts.append(f"Package {k} leaves your unit price as it is and puts the value into the terms.")
            else:
                parts.append(f"Package {k} keeps the price change within {pct:g} % of your offer and puts the rest into the terms.")
    parts.append("")
    if lines.get("mirror"):
        parts.append("Please confirm, and we will place the order.")
    elif lines.get("park"):
        parts.append("Please keep this offer open while we complete our review.")
    elif lines.get("breakdown"):
        parts.append(
            "Could you send us a cost breakdown for this part? And what would you need from us to move further on the price?"
        )
    elif lines.get("closing"):
        parts.append("This is the package we can sign now; if it works for you, we will close on it.")
    elif lines.get("ask_floor"):
        parts.append("What is the best price you could sign on one of these packages?")
    else:
        parts.append("What would you need from us to move further?")
    parts += ["", SIGN_OFF]
    body = "\n".join(parts)
    body = re.sub(r"\n{3,}", "\n\n", body)
    refs = [lines["volume_ref"]] if lines.get("volume_ref") and "volume" in lines and not lines.get("mirror") else []
    for c in decision.claims:
        for r in c.refs:
            if r not in refs:
                refs.append(r)
    subject = f"Re: {case.get('title', 'your offer')}"[:300]
    message = {
        "subject": subject,
        "body": body[:8000],
        "term_sheets": [{"terms": s} for s in sheets],
        "refs": refs[:20],
    }
    return message, used_llm
