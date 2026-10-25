"""LLM mode for the persona bank (`bank.py run --supplier-llm`): Claude reads the buyer's e-mail and
writes the supplier's reply, as the arena's bots do. Prices, floors and the accept / final / walk
decisions stay in the persona's code.

- The reader turns the buyer's prose into structured signals: claims (with or without a data ID),
  the one argument it finds strongest, walk threats, deadlines, tone (insults, condescension,
  dismissive lowballing, appreciation), promises (payment, volume) and questions. Code checks every
  ID against the data package; the persona applies the signals within the bounds in
  `personas.COMMON["llm"]` (see `Persona.apply_signals`).
- The writer turns the persona's decision (status, standing terms, claim verdicts, lines) into an
  e-mail in the persona's voice. Code checks the draft: the standing terms line, every claim verdict,
  every alternative and the final-offer sentence must be there verbatim, and it may state no euro
  figure that the decision does not contain. A draft that fails falls back to the template.

Every call has a hard timeout and a few retries; a failure falls back to code. Tokens and USD are
booked per game and in total, and a budget stops new calls once spent.
"""

import json
import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

from kit.sim import ID_RE

PRICES = {  # USD per million tokens (input, output), as agent/parley/llm.py
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-1": (10.0, 50.0),
}
EURO = re.compile(r"€\s?(\d[\d,]*(?:\.\d+)?)|(\d[\d,]*(?:\.\d+)?)\s?(?:€|EUR\b)")

# Who writes for each persona, and how. `owner` personas sign as the owner, `group` as a key account
# manager of a group; the supplier's own record (owner_type) can turn an owner persona into a group.
VOICES = {
    "mirror": ("group", "Key Account Manager", "measured and reciprocal: you move when they move, and say so plainly"),
    "holder": (
        "owner",
        "Managing Director",
        "firm on price, proud of the workshop's quality, few words, not moved by pressure",
    ),
    "floor_stop": ("group", "Inside Sales", "polite distributor tone, refers to the price list and discount tiers"),
    "short_fuse": ("owner", "Owner", "founder of a small shop, short on time, direct, wants a quick decision"),
    "auction": ("group", "Export Sales Manager", "eager for the business but checks every rival quote internally"),
    "index_lover": (
        "group",
        "Key Account Manager",
        "worried about volatile material prices, keeps steering to a price adjustment clause",
    ),
    "cash_constrained": (
        "owner",
        "Managing Director",
        "warm family-business tone, short of cash, keeps coming back to fast payment",
    ),
    "dup_punisher": (
        "group",
        "Key Account Manager",
        "formal, by the book, wants a data reference for every figure, has a pricing committee",
    ),
    "announced_steps": (
        "group",
        "Sales Manager",
        "friendly, needs management approval and announces what it can do next",
    ),
}
FIRST = [
    "Josef",
    "Thomas",
    "Andrea",
    "Michael",
    "Sabine",
    "Florian",
    "Martina",
    "Stefan",
    "Monika",
    "Georg",
    "Petra",
    "Markus",
]
LAST = [
    "Huber",
    "Brandl",
    "Gruber",
    "Wimmer",
    "Maier",
    "Hofbauer",
    "Lechner",
    "Kastner",
    "Moser",
    "Eder",
    "Pichler",
    "Stadler",
]

STYLE = """Write in English as a Bavarian Mittelstand supplier writes it: correct but slightly German-flavoured
(formal "Dear ...", the occasional German word such as "Preisgleitklausel", "Grüß Gott" or "Servus" only if it fits
the person, sentences like "We have looked at it in detail", "this is not possible for us"). Plain text, no
markdown, no bullet symbols other than "- ", no subject line. 60-170 words before the sign-off."""

WRITER_SYSTEM = (
    """You write one e-mail reply from a supplier to the purchasing team of Hollerau Pumpenwerke GmbH.
The supplier's back office has already decided everything that matters: the status, the standing terms, the price,
the verdict on each data point the buyer cited. You only write the e-mail, in the voice described.

Rules:
- Use every line marked VERBATIM word for word, each on its own line (you may put them in any sensible order and
  add your own sentences around them).
- State no euro amount, percentage, number of days or other figure that is not in the decision. Never promise a
  price, a step or a term the decision does not contain. Do not invent data, documents or people beyond the sign-off.
- Paraphrase the PARAPHRASE lines in your own voice; keep their meaning.
- React like this person would to the buyer's tone and questions (the reader's notes tell you what stood out),
  without changing the decision.
- Refer only to what the buyer actually wrote; if there is no buyer e-mail, do not imply one.
- Greet "Dear Hollerau purchasing team," or, if the buyer signed with a person's name, that person.
- End with a sign-off line, then the signature lines given.
"""
    + STYLE
)

READER_SYSTEM = """You read a buyer's e-mail for a supplier's back office. Extract what the buyer argues and how,
as JSON only (no prose), with exactly these keys:
{"claims": [{"quote": "<= 20 words from the e-mail", "ref": "<data ID cited for it, or null>", "has_figure": true|false,
             "topic": "should_cost|rival_quote|index|quality|delivery|contract|volume|payment|price_history|capacity|other"}],
 "strongest_ref": "<the one cited data ID whose argument is most convincing and tied to the price, or null>",
 "strongest_why": "<= 15 words",
 "walk_threat": 0|1|2,        // 0 none, 1 mentions alternatives or other suppliers, 2 explicit ultimatum / threat to end talks
 "deadline": true|false,      // sets a time limit
 "insult": true|false,        // personal attack, contempt, calling the supplier dishonest or greedy
 "condescending": true|false, // lecturing, patronising, "you must understand", sarcasm
 "lowball_tone": true|false,  // dismisses the quote as absurd / demands a drastic cut without reasons
 "appreciation": true|false,  // sincerely values the relationship, the supplier's work or its step
 "payment_promise": true|false, // promises to pay on time / faster
 "volume_commitment": true|false, // commits to a binding volume
 "asks_breakdown": true|false,
 "questions": ["<= 15 words each, the buyer's real questions"],
 "react_to": "<the phrase, <= 25 words, quoted exactly, that a supplier would react to most>"}
Only list claims the buyer actually makes. Data IDs look like cost:..., quote:..., offer:..., sc:..., idx:...@...,
ctr:..., doc:..., po:..., fc:..., news:..., qn:.... Judge tone as a German Mittelstand supplier would."""


class BudgetExceeded(Exception):
    pass


OPENROUTER = "https://openrouter.ai/api/v1"


def provider(model):
    """`vendor/model` IDs (OpenRouter's) go to OpenRouter, `claude-*` to Anthropic."""
    return "openrouter" if "/" in model else "anthropic"


def load_key(repo, name):
    """A key from the environment or the repo-root .env (never printed)."""
    if os.environ.get(name):
        return os.environ[name]
    env = Path(repo) / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() == name and v.strip():
                return v.strip().strip("\"'")
    return None


def openrouter_usage(key):
    """Spend on the OpenRouter key so far (USD), from GET /key."""
    import httpx

    r = httpx.get(f"{OPENROUTER}/key", headers={"Authorization": f"Bearer {key}"}, timeout=15)
    r.raise_for_status()
    d = r.json().get("data", {})
    return {k: d.get(k) for k in ("usage", "limit", "limit_remaining")}


class _OR:
    """An OpenRouter reply, shaped for `SupplierLLM.call`."""

    def __init__(self, text, tin, tout, usd, finish):
        self.text, self.tin, self.tout, self.usd = text, tin, tout, usd
        self.stop_reason = "refusal" if finish == "content_filter" else finish


def cost(model, tin, tout):
    pin, pout = PRICES.get(model, (4.0, 20.0))
    return tin / 1e6 * pin + tout / 1e6 * pout


class SupplierLLM:
    def __init__(self, keys, model="claude-sonnet-5-5", reader_model=None, budgets=None, timeout=30.0,
                 retries=2, effort="low"):  # fmt: skip
        import anthropic
        import httpx

        self.anthropic, self.httpx = anthropic, httpx
        self.model, self.reader_model = model, reader_model or model
        for prov in {provider(self.model), provider(self.reader_model)}:
            if not keys.get(prov):
                raise RuntimeError(f"no API key for {prov}")
        self.client = (
            anthropic.Anthropic(api_key=keys["anthropic"], max_retries=0, timeout=timeout)
            if keys.get("anthropic")
            else None
        )
        self.or_key = keys.get("openrouter")
        self.or_http = httpx.Client(timeout=timeout) if self.or_key else None
        self.budgets = {"anthropic": 5.0, "openrouter": 5.0, **(budgets or {})}
        self.timeout, self.retries, self.effort = timeout, retries, effort
        self.lock = threading.Lock()
        self.calls, self.failures, self.fallbacks = [], 0, {"writer": 0, "reader": 0, "repaired": 0}
        self.pool = ThreadPoolExecutor(max_workers=24)

    # -- accounting -----------------------------------------------------------------------------------
    def spent(self, prov=None):
        with self.lock:
            return sum(c["usd"] for c in self.calls if prov is None or c["provider"] == prov)

    def exhausted(self, prov=None):
        provs = [prov] if prov else sorted({provider(self.model), provider(self.reader_model)})
        return any(self.spent(p) >= self.budgets[p] for p in provs)

    def summary(self, tag=None):
        with self.lock:
            cs = [c for c in self.calls if tag is None or c["tag"] == tag]
        out = {"calls": len(cs), "input_tokens": sum(c["in"] for c in cs), "output_tokens": sum(c["out"] for c in cs),
               "usd": round(sum(c["usd"] for c in cs), 4)}  # fmt: skip
        for prov in ("anthropic", "openrouter"):
            out[f"{prov}_usd"] = round(sum(c["usd"] for c in cs if c["provider"] == prov), 4)
        for role in ("reader", "writer", "opening"):
            out[f"{role}_usd"] = round(sum(c["usd"] for c in cs if c["role"] == role), 4)
        if tag is None:
            out.update(failures=self.failures, fallbacks=dict(self.fallbacks))
        return out

    # -- one call -------------------------------------------------------------------------------------
    def _or_reasoning(self, model):
        """The model's reasoning settings from OpenRouter's model list (looked up once)."""
        if not hasattr(self, "_or_models"):
            try:
                r = self.or_http.get(f"{OPENROUTER}/models")
                self._or_models = {m["id"]: m.get("reasoning") or {} for m in r.json()["data"]}
            except Exception:  # noqa: BLE001
                self._or_models = {}
        return self._or_models.get(model) or {}

    def _openrouter(self, model, system, user, max_tokens):
        info = self._or_reasoning(model)
        if info.get("mandatory", True):
            reasoning, extra = {"effort": "low", "exclude": True}, 4000
        else:  # optional reasoning: off, as for the Claude writers
            reasoning, extra = {"effort": "none", "exclude": True}, 500
        body = {"model": model, "max_tokens": max_tokens + extra, "usage": {"include": True}, "reasoning": reasoning,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}  # fmt: skip
        r = self.or_http.post(f"{OPENROUTER}/chat/completions", json=body,
                              headers={"Authorization": f"Bearer {self.or_key}", "X-Title": "persona-bank"})  # fmt: skip
        if r.status_code == 429 or r.status_code >= 500:
            raise RuntimeError(f"openrouter {r.status_code}")
        if r.status_code >= 400:
            raise ValueError(f"openrouter {r.status_code}: {r.text[:200]}")
        d = r.json()
        if d.get("error"):
            raise RuntimeError(f"openrouter error: {str(d['error'])[:200]}")
        u = d.get("usage") or {}
        ch = (d.get("choices") or [{}])[0]
        content = (ch.get("message") or {}).get("content") or ""
        return _OR(
            content, u.get("prompt_tokens", 0), u.get("completion_tokens", 0), u.get("cost"), ch.get("finish_reason")
        )

    def _create(self, model, system, user, max_tokens):
        if provider(model) == "openrouter":
            return self._openrouter(model, system, user, max_tokens)
        kw = {}
        if model.startswith("claude-sonnet-5-5"):
            kw["thinking"] = {"type": "between_tools"}  # no thinking: a short e-mail does not need it
        elif model.startswith("claude-haiku"):
            kw["thinking"] = {"type": "disabled"}
        else:  # Opus 5.5 and Fable always think: leave room for it
            max_tokens += 3000
        return self.client.messages.create(model=model, max_tokens=max_tokens, system=system,
                                           messages=[{"role": "user", "content": user}],
                                           output_config={"effort": self.effort}, **kw)  # fmt: skip

    def call(self, role, system, user, tag, max_tokens=700):
        model = self.reader_model if role == "reader" else self.model
        prov = provider(model)
        if self.exhausted(prov):
            raise BudgetExceeded(f"{prov} budget ${self.budgets[prov]:.2f} spent")
        a, err = self.anthropic, None
        for attempt in range(self.retries + 1):
            t0 = time.monotonic()
            fut = self.pool.submit(self._create, model, system, user, max_tokens)
            try:
                r = fut.result(timeout=self.timeout + 2)
            except (
                FutureTimeout,
                a.APITimeoutError,
                a.APIConnectionError,
                a.RateLimitError,
                a.InternalServerError,
                self.httpx.HTTPError,
                RuntimeError,
                json.JSONDecodeError,
            ) as e:
                err = e
            except ValueError:
                with self.lock:
                    self.failures += 1
                raise
            except a.APIStatusError as e:
                if e.status_code < 500 and e.status_code != 429:
                    with self.lock:
                        self.failures += 1
                    raise
                err = e
            else:
                if isinstance(r, _OR):
                    text, tin, tout = r.text, r.tin, r.tout
                    usd = r.usd if r.usd is not None else cost(model, tin, tout)
                else:
                    text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
                    tin, tout = r.usage.input_tokens, r.usage.output_tokens
                    usd = cost(model, tin, tout)
                with self.lock:
                    self.calls.append({"tag": tag, "role": role, "model": model, "provider": prov, "in": tin,
                                       "out": tout, "s": round(time.monotonic() - t0, 2), "usd": usd})  # fmt: skip
                if r.stop_reason == "refusal":
                    raise RuntimeError("refusal")
                return text
            with self.lock:
                self.failures += 1
            if attempt < self.retries:
                time.sleep(1.5 * (attempt + 1) + random.random())
        raise RuntimeError(f"{role} failed after {self.retries + 1} tries: {err!r}")

    # -- the reader -----------------------------------------------------------------------------------
    def read(self, persona, msg, sheets, session, tag):
        body = (msg.get("body") or "").strip()
        if not body:
            return None
        refs = list(dict.fromkeys((msg.get("refs") or []) + ID_RE.findall(body)))
        known = []
        for r in refs[:12]:
            fact = persona.world.facts.get(r) or persona.case.get("facts", {}).get(r)
            if fact is None and r in session["offers"]:
                fact = {"rival_offer": session["offers"][r]}
            known.append(f"- {r}: " + ("NOT IN OUR RECORDS" if fact is None else json.dumps(fact, default=str)[:220]))
        user = (f"We are {persona.name}, supplier of {persona.part} in a {persona.case['type']} negotiation, round "
                f"{getattr(persona, 'round', 0) + 1}. Our standing price: €{persona.stated.get('unit_price', persona.opening):,.2f}.\n"
                f"The buyer's term sheets: {'; '.join(_terms(s) for s in sheets) or 'none'}\n"
                f"Data IDs the buyer cited, as our records hold them:\n{chr(10).join(known) or '- none'}\n\n"
                f"The buyer's e-mail:\n<<<\n{body[:5000]}\n>>>")  # fmt: skip
        try:
            text = self.call("reader", READER_SYSTEM, user, tag, max_tokens=900)
        except BudgetExceeded:
            raise
        except Exception as e:  # noqa: BLE001
            with self.lock:
                self.fallbacks["reader"] += 1
            persona.llm_log.append({"round": persona.round, "reader_failed": repr(e)[:200]})
            return None
        sig = _json(text)
        if sig is None:
            with self.lock:
                self.fallbacks["reader"] += 1
            persona.llm_log.append({"round": persona.round, "reader_failed": f"no JSON: {(text or '')[:120]!r}"})
            return None
        # code checks the IDs: a claim's ref counts only if the buyer really cited it and it exists
        facts = persona.world.facts
        for c in sig.get("claims") or []:
            ref = c.get("ref")
            c["in_records"] = bool(ref) and (
                ref in facts or ref in persona.case.get("facts", {}) or ref in session["offers"]
            )
            c["cited"] = bool(ref) and ref in refs
        if sig.get("strongest_ref") not in refs:
            sig["strongest_ref"] = None
        return sig

    # -- the writer -----------------------------------------------------------------------------------
    def voice(self, persona):
        if getattr(persona, "_voice", None):
            return persona._voice
        kind, title, temper = VOICES.get(persona.key, ("group", "Key Account Manager", "professional"))
        intel = persona.world.intel.get(persona.sup) or {}
        owner_type = (intel.get("owner_type") or "").lower()
        if kind == "owner" and owner_type in ("group", "listed", "corporate"):
            kind, title = "group", "Key Account Manager"
        rng = random.Random(f"voice:{persona.sup}:{persona.key}")
        who = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        staff = intel.get("staff")
        about = (f"{'family-owned' if kind == 'owner' else 'part of a larger group'}"
                 + (f", about {staff} staff" if staff else "") + (f", founded {intel['founded']}" if intel.get("founded") else ""))  # fmt: skip
        if kind == "owner":
            desc = f"{who}, {title} and owner of {persona.name} ({about}). You write personally, as the owner."
        else:
            desc = (
                f"{who}, {title} at {persona.name} ({about}). You write for the company and refer to internal approval."
            )
        persona._voice = {"who": who, "title": title, "kind": kind, "desc": f"{desc} Temperament: {temper}.",
                          "signature": [who, title, persona.name]}  # fmt: skip
        return persona._voice

    def write(self, persona, reply, buyer_msg, sig, tag, opening=False):
        v = self.voice(persona)
        c = getattr(persona, "last_compose", None)
        verbatim, para = [], []
        if opening:
            std = {k: x for k, x in persona.standard.items() if k in persona.case["negotiable"]}
            from personas import describe

            verbatim.append(f"Our quote: unit price €{persona.opening:,.2f} at our standard terms ({describe(std)}).")
            if persona.drivers:
                verbatim.append(f"Our price follows our main cost drivers: {', '.join(persona.drivers)}.")
            para.append("Thank the buyer for the enquiry; the quote PDF is attached.")
            status = "open (first quote)"
        else:
            if not c:
                return None
            verbatim.append(c["terms_line"])
            verbatim += c["verdicts"]
            verbatim += c["alternatives"]
            if c["final"]:
                verbatim.append("This is our final offer.")
            para += c["lines"]
            if c.get("drivers"):
                para.append(c["drivers"])
            status = reply.status
        notes = []
        if sig:
            for k in (
                "insult",
                "condescending",
                "lowball_tone",
                "appreciation",
                "payment_promise",
                "volume_commitment",
                "deadline",
            ):
                if sig.get(k):
                    notes.append(k.replace("_", " "))
            if sig.get("walk_threat"):
                notes.append("walk threat" if sig["walk_threat"] == 2 else "mentions alternatives")
            if sig.get("react_to"):
                notes.append(f'what stood out: "{sig["react_to"]}"')
            if sig.get("questions"):
                notes.append("questions: " + "; ".join(sig["questions"][:3]))
        irr = getattr(persona, "irritation", 0)
        if irr:
            notes.append(f"you are irritated by the buyer's tone (level {irr})")
        user = (f"You are {v['desc']}\n\nDecision (status {status}):\n"
                + "\n".join(f"VERBATIM: {x}" for x in verbatim) + "\n"
                + "\n".join(f"PARAPHRASE: {x}" for x in para) + "\n"
                + (f"Reader's notes on the buyer's e-mail: {'; '.join(notes)}\n" if notes else "")
                + (f"\nThe buyer's e-mail you answer:\n<<<\n{(buyer_msg or {}).get('body', '')[:3000]}\n>>>\n" if buyer_msg else "")
                + "\nSignature lines:\n" + "\n".join(v["signature"]))  # fmt: skip
        try:
            text = self.call("opening" if opening else "writer", WRITER_SYSTEM, user, tag, max_tokens=700)
        except BudgetExceeded:
            raise
        except Exception:  # noqa: BLE001
            text = None
        text = (text or "").strip()
        figures = allowed_figures(verbatim + para)
        for ts in (buyer_msg or {}).get("term_sheets") or []:  # the buyer's own prices may be quoted back
            if (ts.get("terms") or {}).get("unit_price"):
                figures.add(float(ts["terms"]["unit_price"]))
        text, repaired = repair_draft(text, verbatim)
        if repaired:
            with self.lock:
                self.fallbacks["repaired"] += 1
        ok, why = check_draft(text, verbatim, figures)
        if not ok:
            with self.lock:
                self.fallbacks["writer"] += 1
            persona.llm_log.append({"round": getattr(persona, "round", 0), "writer_rejected": why})
            return None
        if v["who"] not in text:
            text += "\n" + "\n".join(v["signature"])
        return text


def _terms(sheet):
    from personas import describe

    return describe(sheet)


def _json(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _norm(s):
    return re.sub(r"\s+", " ", s).strip().lower()


def allowed_figures(lines):
    out = set()
    for line in lines:
        for a, b in EURO.findall(line):
            out.add(float((a or b).replace(",", "")))
    return out


SIGN_OFF = re.compile(
    r"^(kind|best|warm)?\s*regards|^(mit )?freundlichen|^gr(ü|ue)(ß|ss)|^servus|^sincerely|^yours|^back to the machines",
    re.I,
)


def repair_draft(text, verbatim):
    """Put verbatim lines the draft left out back in: the terms line after the greeting, the rest
    before the sign-off. Returns (text, whether anything was added)."""
    if len(text) < 40:
        return text, False
    flat = _norm(text)
    missing = [x for x in verbatim if _norm(x) not in flat]
    if not missing:
        return text, False
    lines = text.splitlines()
    head = [x for x in missing if x.startswith(("Our terms:", "Our quote:"))]
    rest = [x for x in missing if x not in head]
    if head:
        g = next((i for i, x in enumerate(lines) if x.strip()), 0)
        lines[g + 1 : g + 1] = ["", *head]
    if rest:
        k = next((i for i in range(len(lines) - 1, -1, -1) if SIGN_OFF.search(lines[i].strip())), len(lines))
        lines[k:k] = [*rest, ""]
    return "\n".join(lines), True


def check_draft(text, verbatim, figures):
    if len(text) < 40:
        return False, "empty"
    flat = _norm(text)
    for line in verbatim:
        if _norm(line) not in flat:
            return False, f"missing verbatim: {line[:60]}"
    for a, b in EURO.findall(text):
        x = float((a or b).replace(",", ""))
        if not any(abs(x - f) < 0.006 for f in figures):
            return False, f"unapproved figure €{x}"
    return True, ""
