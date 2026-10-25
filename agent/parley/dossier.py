"""What we know about a case and its suppliers before the first e-mail: citable facts with their data
IDs (checked against facts.json, the Sunday release and the case's own facts) and a profile of each
supplier from its notes, letters, news, register data and payment history."""

import functools
import json
import logging
import os
import re
import statistics
import threading
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from . import rules
from .analyst import CASH_STRICT
from .kit.data import DataPackage

ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger("parley")
SERIES_RE = re.compile(r"\b([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+)\b")
DB_LOCK = threading.Lock()  # one SQLite connection serves every case thread of the final
SHOULD_COST_MARKUP = 1.02
FAVOURABLE_NEWS = {"free_capacity", "short_time_work", "new_plant", "supply_normalises", "customer_loss"}
CASH_NEWS = {"credit_downgrade", "credit_watch", "restructuring", "short_time_work", "insolvency"}
RISK_NEWS = {"insolvency"}
TIGHT_NEWS = {"allocation", "full_order_book", "big_order", "chip_shortage"}
EASED_NEWS = {"supply_normalises"}
CASH_WORDS = re.compile(
    r"cash ?flow|liquidity|faster payment|pay (us )?faster|prepayment|credit line|short of cash|tight on cash"
    r"|most now is cash|pay (?:our )?invoices within|pay earlier|bank is watching",
    re.I,
)
CASH_WORDS_WIDE = re.compile(
    CASH_WORDS.pattern + r"|within (?:10|ten) days|cash discount|pay earlier|\bis cash\b|bank is watching", re.I
)
CAPACITY_FREE = re.compile(r"free capacity|idle|short-time|kurzarbeit|utili[sz]ation (is )?(low|down)|empty", re.I)
CAPACITY_CLAIM = re.compile(
    r"short-time work|kurzarbeit|under-?utili[sz]|utili[sz]ation (?:is )?(?:low|down)|free capacity|idle capacity", re.I
)
RISK_WORDS = re.compile(r"insolven|bankrupt|provisional administrator", re.I)
EASED_WORDS = re.compile(r"back to normal|normali[sz]e[sd]?\b", re.I)
LINE_CASH = re.compile(r"credit insurer|withdraws? (?:its )?(?:cover|limits)", re.I)
CAPACITY_TIGHT = re.compile(r"sold out|fully booked|allocat|no free capacity|bottleneck", re.I)
HEDGE = re.compile(r"(?:covered|hedged|fixed prices?)\b[^.]{0,60}?until", re.I)
HEDGE_WIDE = re.compile(r"(?:covered|hedged|fixed prices?|bought\b[^.]{0,60}?forward)\b[^.]{0,60}?until", re.I)
# PARLEY_HEDGE_DOCS: a cover named by year ("secured ... for 2027", "bought forward for 2027"), not only "until".
HEDGE_DOCS = re.compile(
    HEDGE_WIDE.pattern
    + r"|bought\b[^.]{0,40}?forward\b[^.]{0,60}?\bfor 20\d\d"
    + r"|secured\b[^.]{0,60}?\bfor 20\d\d(?=[^.]{0,80}?(?:fixed price|hedg|forward))"
    + r"|(?:fixed price|hedg|forward)[^.]{0,80}?secured\b[^.]{0,60}?\bfor 20\d\d",
    re.I,
)
ENERGY_SERIES = ("PPI-D351", "PPI-D35", "SMARD-DA", "WB-NGEU")
NOT_MATERIAL = re.compile(r"^(?:LCI|ECB)-")
HEDGE_NEWS = {"energy_contract": ("PPI-D351", "PPI-D35", "SMARD-DA")}
GENERIC_MATERIAL = re.compile(r"\bmaterial", re.I)
MATERIAL_SERIES = {
    "copper": ("CU-NOTATION", "WB-CU"),
    "nickel": ("WB-NI", "AS-14404", "AS-14301"),
    "alloy": ("AS-14404", "AS-14301"),
    "stainless": ("AS-14404", "AS-14301"),
    "alumin": ("WB-AL",),
    "steel": ("PPI-C241",),
    r"bars?\b": ("PPI-C2431",),
    "electric": ("PPI-D351", "PPI-D35", "SMARD-DA"),
    "power": ("PPI-D351", "PPI-D35", "SMARD-DA"),
    "energy": ("PPI-D351", "PPI-D35", "SMARD-DA", "WB-NGEU"),
    "gas": ("WB-NGEU", "PPI-D35"),
}
MATERIAL_SERIES_WIDE = {**MATERIAL_SERIES, "bar": ("PPI-C2431",), "cold-drawn": ("PPI-C2431",)}
CORPORATE_NOTES = re.compile(r"pricing committee|key account", re.I)
DISTRIBUTOR_CATEGORIES = {"C-parts", "raw material"}
SKONTO_RATE_CAP = 0.4
COST_PAST_CAP, COST_PAST_START = 0.92, 0.88
SIGNALS = ("cash", "hedge", "cost", "type", "freeze")
DEFAULT_SIGNALS = ("cash", "cost", "hedge")


def cash_words(wide):
    """PARLEY_CASH_STRICT: only real cash evidence counts, not a stock phrase about faster payment."""
    from .policy import flag

    if flag("PARLEY_CASH_STRICT"):
        return CASH_STRICT
    return CASH_WORDS_WIDE if wide else CASH_WORDS


def signal(name):
    """Dossier signals behind a switch: PARLEY_SIG="cash,hedge", "all" or "none"; unset = the defaults."""
    raw = rules.env("PARLEY_SIG")
    if raw is None:
        return name in DEFAULT_SIGNALS
    raw = raw.strip().lower()
    if raw == "all":
        return True
    return name in {x.strip() for x in raw.split(",")}


FREEZE = re.compile(r"price list|preisliste|list price|frozen|freeze", re.I)
PROFILE_FIELDS = ROOT / "cache" / "profile_fields.json"
TRUSTED = ("high", "medium")


def pf_on(name):
    """One switch per variant: PARLEY_PF_FORK, _FREEZE, _FACTS (profile fields), _HOLD, _MOVER (behaviour)."""
    raw = rules.env(f"PARLEY_PF_{name.upper()}")
    if raw is None:
        raw = PF_ADOPTED.get(name.upper(), "")
    return raw.strip().lower() in ("1", "on", "true", "yes")


# Profile-field variants that passed the gate: on unless PARLEY_PF_<NAME>=0.
PF_ADOPTED = {"MOVER": "1"}


@functools.cache
def _profile_fields_file():
    try:
        data = json.loads(PROFILE_FIELDS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    data.pop("_meta", None)
    return data


def profile_fields(sid):
    """The supplier's scalar profile fields with confidence high or medium; low ones are dropped."""
    row = _profile_fields_file().get(sid) or {}
    out = {}
    for name, f in (row.get("fields") or {}).items():
        if isinstance(f, dict) and f.get("value") is not None and f.get("confidence") in TRUSTED:
            out[name] = f["value"]
    return out


def still_valid(until, today=None):
    month = (today or date.today()).strftime("%Y-%m")
    return bool(until) and str(until)[:7] >= month


def default_data_dir():
    for cand in (
        os.environ.get("ARENA_DATA"),
        ROOT / "data" / "package",
        ROOT.parent / "internal" / "briefing" / "tacto" / "repo" / "data",
    ):
        if cand and (Path(cand) / "facts.json").is_file():
            return Path(cand)
    return None


def load_package(path=None):
    path = Path(path) if path else default_data_dir()
    if not path:
        return None
    pkg = DataPackage(path)
    pkg.lookup("")
    return pkg


@dataclass
class Claim:
    kind: str
    refs: list
    sentence: str
    strength: float
    figure: float | None = None
    topic: str = ""
    supplier: str | None = None

    @property
    def key(self):
        return self.kind + ":" + ",".join(self.refs)


@dataclass
class Profile:
    supplier_id: str
    name: str = ""
    owner_type: str = ""
    staff: int | None = None
    cash_short: bool = False
    impatient: bool = False
    capacity_free: bool = False
    capacity_tight: bool = False
    price_freeze: bool = False
    corporate: bool = False
    insolvent: bool = False
    market: list = field(default_factory=list)
    we_pay_late_days: float = 0.0
    hedged_series: list = field(default_factory=list)
    drivers: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    interest_rate: float = 0.08
    full_cost: float | None = None
    should_cost: float | None = None
    reasons: list = field(default_factory=list)
    fields: dict = field(default_factory=dict)


def skonto_rate(row):
    """The yearly rate a supplier's own cash discount implies (2 % for paying 20 days early is 36 %)."""
    pct, early, net = row.get("skonto_pct"), row.get("skonto_days"), row.get("payment_days")
    try:
        pct, early, net = float(pct), float(early), float(net)
    except (TypeError, ValueError):
        return 0.0
    if pct <= 0 or net <= early:
        return 0.0
    return min(SKONTO_RATE_CAP, pct / 100 * 360 / (net - early))


def apply_persona(prof, persona):
    """Merge the cached plan-model profile into a rule profile (flags only, never a price)."""
    if not persona:
        return
    if persona.get("cash_short") and not prof.cash_short:
        prof.cash_short = True
        if signal("cash"):
            prof.interest_rate = max(prof.interest_rate, 0.14)
    prof.impatient = prof.impatient or persona.get("patience") == "low"
    for s in persona.get("hedged_series") or []:
        if s not in prof.hedged_series:
            prof.hedged_series.append(s)


LEGAL_WORDS = {"gmbh", "co.", "kg", "ag", "se", "sp.", "z", "o.o.", "s.r.o.", "a.s.", "&", "und", "and", "sohn"}


def name_words(name):
    return {w.strip("().,;") for w in re.split(r"[\s,]+", (name or "").lower())} - LEGAL_WORDS


def month_add(month, n):
    y, m = map(int, month.split("-"))
    m += n
    while m <= 0:
        y, m = y - 1, m + 12
    while m > 12:
        y, m = y + 1, m - 12
    return f"{y:04d}-{m:02d}"


def fmt_eur(v):
    return f"{v:.2f}"


class Dossier:
    def __init__(self, case, package: DataPackage | None):
        self.case, self.pkg = case, package
        self.facts = dict(package.facts) if package else {}
        self.facts.update(case.get("facts") or {})
        self.parts = case.get("parts", [])
        self.part_ids = [p.split(":", 1)[1] for p in self.parts]
        self._series = None
        self._market = None
        self._common = None
        self._master = {}
        self.profiles = {s: self._safe_profile(s) for s in case["suppliers"]}

    # ---- facts --------------------------------------------------------------------------------

    def fact(self, ref):
        return self.facts.get(ref)

    def known(self, ref):
        if ref in self.facts:
            return True
        return bool(self.pkg and self.pkg.lookup(ref))

    def citable(self, ref):
        return ref in self.facts or ref in set(self.case.get("refs", []))

    def release_rows(self, table):
        """Rows of the Sunday release (CSV files next to its facts.json), which are not in the SQLite."""
        if not self.pkg or not getattr(self.pkg, "release", None):
            return []
        return [r for r in self.pkg._ids.values() if r.get("table") == table and r.get("root")]

    def news(self, sid):
        rows = self._q("SELECT id, date, topic, headline, body FROM news WHERE supplier_id = ?", (sid,))
        rows += [r for r in self.release_rows("news") if r.get("supplier_id") == sid]
        return sorted(rows, key=lambda r: r.get("date") or "", reverse=True)

    def documents(self, sid):
        rows = self._q("SELECT id, date, kind, subject, file FROM documents WHERE supplier_id = ?", (sid,))
        rows += [r for r in self.release_rows("documents") if r.get("supplier_id") == sid]
        return sorted(rows, key=lambda r: r.get("date") or "", reverse=True)

    @property
    def common_words(self):
        """Words that occur in more than one supplier name: no use for telling suppliers apart."""
        if self._common is None:
            seen, common = set(), set()
            for r in self._q("SELECT name FROM suppliers"):
                words = name_words(r["name"])
                common |= seen & words
                seen |= words
            self._common = common
        return self._common

    def market_items(self):
        """News and documents without a supplier (market news, our own memos), newest first, with text."""
        if self._market is None:
            rows = self._q(
                "SELECT id, date, topic, headline, body FROM news WHERE supplier_id IS NULL OR supplier_id = ''"
            )
            rows += [r for r in self.release_rows("news") if not r.get("supplier_id")]
            out = [{**r, "kind": "news", "text": f"{r.get('headline') or ''}. {r.get('body') or ''}"} for r in rows]
            docs = self._q(
                "SELECT id, date, kind, subject, file FROM documents WHERE supplier_id IS NULL OR supplier_id = ''"
            )
            docs += [r for r in self.release_rows("documents") if not r.get("supplier_id")]
            for d in docs:
                try:
                    text = (self.pkg.document(d.get("id")) if self.pkg else "") or ""
                except Exception as e:  # noqa: BLE001 - one unreadable document must not cost the profile
                    log.warning("document %s: %r", d.get("id"), e)
                    text = ""
                out.append({**d, "topic": d.get("kind") or "", "text": text})
            self._market = sorted(out, key=lambda r: r.get("date") or "", reverse=True)
        return self._market

    def _market_lines(self, item, p):
        """Whether a market item concerns this supplier, and the lines that name it: (related, named).
        Related: it names the supplier, is about one of its cost drivers, or is our own memo."""
        text = item["text"] or ""
        lines = [x.strip() for x in re.split(r"[\n;]", text) if x.strip()]
        names = [w for w in name_words(p.name) if len(w) > 3 and w not in self.common_words]
        named = [x for x in lines if names and any(n in x.lower() for n in names)]
        related = bool(named) or item.get("kind") == "memo" or any(s in text for s in p.drivers)
        for word, series in MATERIAL_SERIES.items():
            if re.search(rf"\b{word}", text, re.I) and any(s in p.drivers for s in series):
                related = True
        return related, named

    def _q(self, sql, params=()):
        if not self.pkg or self.pkg.db is None:
            return []
        try:
            with DB_LOCK:
                return self.pkg.query(sql, params, limit=500)
        except Exception as e:  # noqa: BLE001 - a data error must not stop the case
            log.warning("query failed (%r): %s %r", e, sql.split(" FROM ", 1)[-1][:60], params)
            return []

    @property
    def series(self):
        if self._series is None:
            out = {}
            for ref, f in self.facts.items():
                if ref.startswith("idx:") and f.get("value") is not None:
                    out.setdefault(f["series"], {})[f["month"]] = (ref, float(f["value"]))
            self._series = out
        return self._series

    def last_month(self):
        months = [m for s in self.series.values() for m in s]
        return max(months) if months else None

    def series_name(self, series):
        rows = self._q("SELECT name FROM index_series WHERE id = ?", (series,))
        return (rows[0].get("name") if rows else None) or series

    def best_decline(self, series):
        """The steepest fall of one series in a window the arena accepts: two months at most 12 apart,
        ending within the last 12 months of the data. Recent windows first."""
        for recent in (11,):
            d = self._decline(series, recent)
            if d:
                return d
        return None

    def _decline(self, series, recent):
        values = self.series.get(series)
        last = self.last_month()
        if not values or not last:
            return None
        floor_end = month_add(last, -recent)
        best = None
        for end, (end_ref, end_v) in values.items():
            if end < floor_end:
                continue
            for k in range(1, 13):
                start = month_add(end, -k)
                if start not in values:
                    continue
                start_ref, start_v = values[start]
                if not start_v:
                    continue
                chg = (end_v / start_v - 1) * 100
                prefer = (round(chg, 1), -k)
                if best is None or prefer < best[0]:
                    best = (prefer, chg, start, end, start_ref, end_ref)
        if not best or best[1] > -1.0:
            return None
        _, chg, start, end, a, b = best
        return {"series": series, "change": chg, "start": start, "end": end, "refs": [a, b]}

    # ---- profile ------------------------------------------------------------------------------

    def _safe_profile(self, sid):
        """A data error must not stop the case: the supplier then plays with a plain profile."""
        try:
            return self._profile(sid)
        except Exception as e:  # noqa: BLE001
            log.warning("profile of %s failed (%r): plain profile", sid, e)
            return Profile(supplier_id=sid, reasons=[f"profile unavailable: {e.__class__.__name__}"])

    def _profile(self, sid):
        p = Profile(supplier_id=sid)
        rows = self._q("SELECT * FROM suppliers WHERE id = ?", (sid,))
        if rows:
            p.name = rows[0]["name"]
        intel = self._q("SELECT * FROM supplier_intel WHERE supplier_id = ?", (sid,))
        if intel:
            i = intel[0]
            p.owner_type, p.staff = i.get("owner_type") or "", i.get("staff")
            if (i.get("equity_ratio_pct") or 100) < 20 or (i.get("credit_index") or 0) > 280:
                p.cash_short = True
                p.reasons.append(
                    f"weak balance sheet (equity {i.get('equity_ratio_pct')} %, credit index {i.get('credit_index')})"
                )
            if (i.get("credit_index") or 0) > (i.get("credit_index_12m_ago") or 999) + 20:
                p.cash_short = True
                p.reasons.append("credit index worsened in 12 months")
            if p.owner_type in ("family", "founder", "owner") and (p.staff or 999) < 60:
                p.impatient = True
                p.reasons.append(f"small {p.owner_type} business ({p.staff} staff)")
        for part in self.part_ids:
            for r in self._q("SELECT series_id, weight FROM cost_drivers WHERE part_id = ?", (f"part:{part}",)):
                p.drivers[r["series_id"]] = max(p.drivers.get(r["series_id"], 0), r["weight"] or 0)
        tight_at, eased_at = [], []
        for n in self.news(sid):
            if n["topic"] in CASH_NEWS:
                p.cash_short = True
                p.reasons.append(f"news: {n['headline']}")
            if n["topic"] in RISK_NEWS:
                p.insolvent = True
            if n["topic"] in ("free_capacity", "short_time_work", "customer_loss"):
                p.capacity_free = True
            if n["topic"] in TIGHT_NEWS:
                p.capacity_tight = True
                tight_at.append(n.get("date") or "")
                p.reasons.append(f"news: {n['headline']}")
            if n["topic"] in EASED_NEWS:
                eased_at.append(n.get("date") or "")
                p.reasons.append(f"news: {n['headline']}")
        for part in self.part_ids:
            for r in self._q("SELECT series_id, weight FROM cost_drivers WHERE part_id = ?", (f"part:{part}",)):
                p.drivers[r["series_id"]] = max(p.drivers.get(r["series_id"], 0), r["weight"] or 0)
        if signal("hedge"):
            for n in self.news(sid):
                for s in HEDGE_NEWS.get(n["topic"], ()):
                    if s in self.series and s not in p.hedged_series:
                        p.hedged_series.append(s)
        late = self._q(
            "SELECT AVG(julianday(paid_date) - julianday(due_date)) AS d FROM invoices "
            "WHERE supplier_id = ? AND paid_date IS NOT NULL AND paid_date >= '2026-01-01'",
            (sid,),
        )
        if late and late[0]["d"]:
            p.we_pay_late_days = round(late[0]["d"], 1)
        for d in self.documents(sid)[:12]:
            try:
                text = (self.pkg.document(d.get("id")) if self.pkg else "") or ""
            except Exception as e:  # noqa: BLE001 - one unreadable document must not cost the profile
                log.warning("document %s: %r", d.get("id"), e)
                text = ""
            p.notes.append({"id": d["id"], "date": d["date"], "kind": d["kind"], "text": text[:1500]})
            self._read_text(p, text)
            if signal("type") and CORPORATE_NOTES.search(text or ""):
                p.corporate = True
            if CAPACITY_TIGHT.search(text or ""):
                tight_at.append(d.get("date") or "")
        if signal("cash") or signal("hedge"):
            for q in self._q("SELECT remarks FROM quotes WHERE supplier_id = ?", (sid,)):
                self._read_text(p, q.get("remarks") or "")
        if signal("type") and rows and rows[0].get("category") in DISTRIBUTOR_CATEGORIES:
            p.corporate = True
        for item in self.market_items():
            related, named = self._market_lines(item, p)
            if not related:
                continue
            p.market.append(item["id"])
            p.notes.append(
                {
                    "id": item["id"],
                    "date": item["date"],
                    "kind": item.get("kind") or "news",
                    "text": item["text"][:1500],
                }
            )
            mine = " ".join(named)
            if RISK_WORDS.search(mine):
                p.insolvent = p.cash_short = True
                p.reasons.append(f"market: {item['id']}")
            if LINE_CASH.search(mine) or cash_words(False).search(mine):
                p.cash_short = True
            if EASED_WORDS.search(mine):
                eased_at.append(item.get("date") or "")
        if eased_at and max(eased_at) > max(tight_at, default=""):
            p.capacity_tight = False
        sc = self.should_cost(sid)
        if sc:
            p.should_cost = sc[1]
            p.full_cost = round(sc[1] / SHOULD_COST_MARKUP, 2)
        family = p.owner_type in ("family", "founder", "owner") and not p.corporate
        unknown = not p.owner_type
        notes_only = (
            (rules.env("PARLEY_NOTES_RATE") or "0") == "0"
            and p.cash_short
            and not p.insolvent
            and any("notes mention" in r for r in p.reasons)
            and all("notes mention" in r for r in p.reasons if "cash" in r or "notes" in r or "market" in r)
        )
        p.interest_rate = 0.14 if p.cash_short and not notes_only else (0.095 if family or unknown else 0.065)
        if signal("cash") and rows:
            p.interest_rate = max(p.interest_rate, skonto_rate(rows[0]))
        if any(pf_on(n) for n in ("fork", "freeze", "facts", "hold")):
            p.fields = profile_fields(sid)
        return p

    def past_prices(self, sid):
        """The supplier's own order and quote prices for the case's parts."""
        out = []
        for ref, f in self.facts.items():
            if ref.startswith(("po:", "quote:")) and f.get("supplier") == sid and f.get("part") in self.parts[:1]:
                if f.get("unit_price"):
                    out.append(float(f["unit_price"]))
        return out

    def _cost_from_prices(self, p, sid, sc):
        """A should-cost row of another plant (or a German cost base) says little about a supplier
        abroad: never above 92 % of what it already sells at. No row at all: start from 88 % of it."""
        past = self.past_prices(sid)
        if not past:
            return
        low = min(past)
        if sc:
            row = self._q("SELECT supplier_id FROM should_cost WHERE id = ?", (sc[0],))
            if row and row[0]["supplier_id"] == sid:
                return
            if sc[1] > COST_PAST_CAP * low:
                p.should_cost = p.full_cost = round(COST_PAST_CAP * low, 2)
                p.reasons.append(f"should-cost of another plant capped at {COST_PAST_CAP:.0%} of its prices")
        else:
            p.full_cost = round(COST_PAST_START * low, 2)

    def _read_text(self, p, text):
        if not text:
            return
        wide = signal("hedge")
        m = cash_words(signal("cash")).search(text)
        if m and not p.cash_short:
            p.cash_short = True
            p.reasons.append(f"notes mention '{m.group(0)}'")
        if CAPACITY_FREE.search(text):
            p.capacity_free = True
        if CAPACITY_TIGHT.search(text):
            p.capacity_tight = True
        if FREEZE.search(text):
            p.price_freeze = True
        from .policy import flag

        rx = (HEDGE_DOCS if flag("PARLEY_HEDGE_DOCS") else HEDGE_WIDE) if wide else HEDGE
        for m in rx.finditer(text):
            window = text[max(0, m.start() - 80) : m.end() + 10]
            named = set(SERIES_RE.findall(window))
            for word, series in (MATERIAL_SERIES_WIDE if wide else MATERIAL_SERIES).items():
                if re.search(rf"\b{word}", window, re.I):
                    named.update(series)
            if wide and not named and not p.hedged_series and GENERIC_MATERIAL.search(window):
                named.update(
                    s for s, w in p.drivers.items() if w >= 0.2 and not NOT_MATERIAL.match(s) and s not in ENERGY_SERIES
                )
            for s in sorted(named):
                if s in self.series and s not in p.hedged_series:
                    p.hedged_series.append(s)

    def observe(self, sid, text):
        """Update a profile from a supplier e-mail (series it names, hedges, cash)."""
        p = self.profiles[sid]
        for s in SERIES_RE.findall(text or ""):
            if s in self.series and s not in p.drivers:
                p.drivers[s] = 0.2
        self._read_text(p, text or "")
        for m in re.finditer(r"([A-Z][A-Z0-9-]+)[^.]{0,40}(?:covered|hedged)", text or ""):
            if m.group(1) in self.series and m.group(1) not in p.hedged_series:
                p.hedged_series.append(m.group(1))

    def master_terms(self, sid):
        """The supplier's terms from our vendor master: the fallback when a quote leaves them out."""
        if sid not in self._master:
            self._master[sid] = self._master_terms(sid)
        return dict(self._master[sid])

    def _master_terms(self, sid):
        rows = self._q("SELECT payment_days, incoterm, standard_lead_time_days FROM suppliers WHERE id = ?", (sid,))
        if not rows:
            return {}
        r = rows[0]
        return {
            "payment_days": r["payment_days"],
            "incoterm": r["incoterm"],
            "lead_time_days": r["standard_lead_time_days"],
        }

    def should_cost(self, sid):
        for part in self.parts:
            rows = self._q(
                "SELECT id, unit_cost, price_unit, supplier_id FROM should_cost WHERE part_id = ? "
                "ORDER BY (supplier_id = ?) DESC, estimate_date DESC",
                (part, sid),
            )
            for r in rows:
                if r["id"] in self.facts:
                    return r["id"], float(self.facts[r["id"]].get("unit_cost") or r["unit_cost"])
        for ref, f in self.facts.items():
            if ref.startswith("cost:") and f.get("part") in self.parts and f.get("unit_cost"):
                return ref, float(f["unit_cost"])
        return None

    # ---- the claim bank ---------------------------------------------------------------------

    def claims(self, sid):
        p = self.profiles[sid]
        out: list[Claim] = []
        refs = list(self.case.get("refs", []))
        used = set()

        notice = self._notice_claim(sid)
        if notice:
            out.append(notice)
            used.update(notice.refs)

        sc = self.should_cost(sid)
        if sc:
            out.append(
                Claim(
                    "should_cost",
                    [sc[0]],
                    f"Our bottom-up should-cost estimate for this part is EUR {fmt_eur(sc[1])} per unit ({sc[0]}).",
                    9,
                    sc[1],
                    "cost",
                    sid,
                )
            )
            used.add(sc[0])

        alt = self.case["buyer"].get("alternative") or {}
        if (
            alt.get("ref", "")
            and str(alt.get("ref")).startswith("quote:")
            and (alt.get("terms") or {}).get("unit_price")
            and not self.insolvent((self.fact(alt["ref"]) or {}).get("supplier"))
        ):
            price = float(alt["terms"]["unit_price"])
            out.append(
                Claim(
                    "alt_quote",
                    [alt["ref"]],
                    f"We hold another quote for this part at EUR {fmt_eur(price)} per unit ({alt['ref']}).",
                    9.2,
                    price,
                    "quote",
                    sid,
                )
            )
            used.add(alt["ref"])

        for series, weight in sorted(p.drivers.items(), key=lambda kv: -kv[1]):
            if series in p.hedged_series:
                continue
            d = self.best_decline(series)
            if not d:
                continue
            name = self.series_name(series)
            label = f"{name} ({series})" if not re.search(r"\d", name) and len(name) < 70 else f"The {series} index"
            out.append(
                Claim(
                    "index",
                    d["refs"],
                    f"{label} changed {d['change']:.1f}% from {d['start']} to {d['end']} ({d['refs'][0]}, {d['refs'][1]}).",
                    6 + min(2.0, weight * abs(d["change"]) / 3),
                    d["change"],
                    f"idx:{series}",
                    sid,
                )
            )
            used.update(d["refs"])

        quarters = sorted((r, f) for r, f in self.facts.items() if r.startswith(f"sc:{sid.split(':', 1)[1]}@"))
        if len(quarters) >= 2:
            last2 = quarters[-2:]
            otd = statistics.mean(float(f.get("otd_pct") or 0) for _, f in last2)
            if otd < 95:
                ids = [r for r, _ in last2]
                out.append(
                    Claim(
                        "otd",
                        ids,
                        f"Your on-time delivery averaged {otd:.1f}% over the last two scorecard quarters ({ids[0]}, {ids[1]}).",
                        5 + (95 - otd) / 10,
                        otd,
                        "sc",
                        sid,
                    )
                )
                used.update(ids)

        for n in self.news(sid):
            if n["topic"] in FAVOURABLE_NEWS and n["id"] in self.facts:
                out.append(
                    Claim(
                        "news",
                        [n["id"]],
                        f"We read that {n['headline'].rstrip('.')} ({n['id']}).",
                        6,
                        None,
                        "news",
                        sid,
                    )
                )
                used.add(n["id"])
                break

        letter = self._capacity_letter(sid, used)
        if letter and not any(c.kind == "news" for c in out):
            out.append(letter)
            used.update(letter.refs)

        fc = self._forecast()
        if fc and fc[1] > 0:
            out.append(
                Claim(
                    "forecast",
                    [fc[0]],
                    f"Our latest forecast shows demand for this part up {fc[1]:.1f}% ({fc[0]}).",
                    4,
                    fc[1],
                    "fc",
                    sid,
                )
            )
            used.add(fc[0])

        jump = self._po_jump(sid)
        if jump:
            (lo_id, lo_date, lo), (hi_id, hi_date, hi) = jump
            text = (
                f"Our orders for this part rose from EUR {fmt_eur(lo)} in {lo_date[:7]} to EUR {fmt_eur(hi)} "
                f"in {hi_date[:7]} ({lo_id}, {hi_id}); you already raised the price within the last year."
            )
            strength = 5.5 if self.case.get("type") in ("price_increase", "raw_material", "renewal") else 3
            out.append(Claim("po", [lo_id, hi_id], text, strength, lo, "po", sid))
            used.update([lo_id, hi_id])

        out.extend(self._unknown_refs(sid, refs, used))
        out.sort(key=lambda c: -c.strength)
        return out

    def _notice_claim(self, sid):
        doc = self.case.get("trigger_doc")
        if not doc:
            return None
        ctrs = [r for r in self.case.get("refs", []) if r.startswith("ctr:") and "#" not in r]
        rows = self._q("SELECT contract_id FROM suppliers WHERE id = ?", (sid,))
        if rows and rows[0]["contract_id"]:
            ctrs.insert(0, rows[0]["contract_id"])
        d = self.fact(doc)
        for ctr in ctrs:
            c = self.fact(ctr)
            if d and c:
                if d.get("supplier") not in (None, sid) or c.get("supplier") not in (None, sid):
                    continue
                if d.get("days") is not None and c.get("days") and d["days"] < c["days"]:
                    return Claim(
                        "notice",
                        [doc, ctr],
                        f"Your letter ({doc}) gives {d['days']} days of notice, while our framework agreement ({ctr}) requires {c['days']} days.",
                        10,
                        d["days"],
                        "notice",
                        sid,
                    )
                return None
            if not d and not c and doc in self.case.get("refs", []) and ctr in self.case.get("refs", []):
                return Claim(
                    "notice",
                    [doc, ctr],
                    f"Your letter ({doc}) gives less notice than our agreement provides for ({ctr}).",
                    8,
                    None,
                    "notice",
                    sid,
                )
        return None

    def insolvent(self, sid):
        if not sid:
            return False
        if sid in self.profiles:
            return self.profiles[sid].insolvent
        return any(n["topic"] in RISK_NEWS for n in self.news(sid))

    def _capacity_letter(self, sid, used):
        """A letter of the supplier's own that admits idle capacity (short-time work, low utilisation)."""
        for n in self.profiles[sid].notes:
            if n["id"] in used or (self.fact(n["id"]) or {}).get("supplier") != sid:
                continue
            m = CAPACITY_CLAIM.search(n["text"] or "")
            if m:
                return Claim(
                    "capacity",
                    [n["id"]],
                    f"Your own letter ({n['id']}) mentions {m.group(0).lower()}: you have capacity to fill, "
                    "and our volume fills it.",
                    5.5,
                    None,
                    "capacity",
                    sid,
                )
        return None

    def _forecast(self):
        best = None
        for ref, f in self.facts.items():
            if ref.startswith("fc:") and f.get("part") in self.parts and f.get("change_pct") is not None:
                if best is None or ref > best[0]:
                    best = (ref, float(f["change_pct"]))
        return best

    def _po_jump(self, sid):
        """The lowest order price of the last 12 months against the latest one, if the latest is at
        least 2% higher: the supplier's own last price jump."""
        rows = []
        for ref, f in self.facts.items():
            if (
                ref.startswith("po:")
                and f.get("supplier") == sid
                and f.get("part") in self.parts[:1]
                and f.get("unit_price")
            ):
                rows.append((f.get("date", ""), ref, float(f["unit_price"])))
        if len(rows) < 2:
            return None
        rows.sort()
        latest = rows[-1]
        year_ago = f"{int(latest[0][:4]) - 1}{latest[0][4:]}"
        window = [r for r in rows if r[0] >= year_ago]
        low = min(window, key=lambda r: (r[2], r[0]))
        if latest[2] < low[2] * 1.02:
            return None
        return (low[1], low[0], low[2]), (latest[1], latest[0], latest[2])

    def _unknown_refs(self, sid, refs, used):
        """Refs the case points to whose values we cannot see (e.g. sandbox facts): cite them without
        a figure, so the arena takes the data's own value."""
        out = []
        po = [r for r in refs if r.startswith("po:") and r not in used and not self.fact(r)]
        if len(po) >= 2:
            out.append(
                Claim(
                    "po",
                    po[:8],
                    f"We compared your offer with our recent order prices for this part ({', '.join(po[:8])}).",
                    3,
                    None,
                    "po",
                    sid,
                )
            )
        tag = sid.split(":", 1)[1]
        sc = [r for r in refs if r.startswith("sc:") and tag in r and r not in used and not self.fact(r)]
        if sc:
            ids = ", ".join(sc[-2:])
            text = f"We have reviewed your delivery performance in our supplier scorecard ({ids})."
            out.append(Claim("otd", sc[-2:], text, 4, None, "sc", sid))
        for r in refs:
            if r in used or r.startswith(("po:", "doc:", "ctr:", "part:", "sup:", "sc:")):
                continue
            if self.fact(r) and not r.startswith("news:"):
                continue
            if r.startswith("cost:"):
                out.append(
                    Claim(
                        "should_cost",
                        [r],
                        f"Our bottom-up should-cost estimate for this part is on file ({r}) and sits below the current offers.",
                        7,
                        None,
                        "cost",
                        sid,
                    )
                )
            elif r.startswith("sc:") and tag in r:
                out.append(
                    Claim(
                        "otd",
                        [r],
                        f"We have reviewed your delivery performance in our supplier scorecard ({r}).",
                        4,
                        None,
                        "sc",
                        sid,
                    )
                )
            elif r.startswith("fc:"):
                out.append(
                    Claim(
                        "forecast",
                        [r],
                        f"Our demand forecast ({r}) confirms the volume we plan with you.",
                        4,
                        None,
                        "fc",
                        sid,
                    )
                )
            elif r.startswith("news:") and self.fact(r):
                f = self.fact(r)
                if f.get("supplier") not in (None, "", sid) or f.get("topic") in TIGHT_NEWS | RISK_NEWS | {"noise"}:
                    continue
                headline = str(f.get("headline") or "").rstrip(".")
                if not headline:
                    continue
                out.append(
                    Claim(
                        "news",
                        [r],
                        f"We read that {headline} ({r}).",
                        5 if f.get("topic") in FAVOURABLE_NEWS else 4,
                        None,
                        "news",
                        sid,
                    )
                )
            elif r.startswith("news:"):
                out.append(
                    Claim(
                        "news",
                        [r],
                        f"We have also taken note of the recent news about your company ({r}).",
                        4,
                        None,
                        "news",
                        sid,
                    )
                )
        return out

    def summary(self):
        return {
            "case": self.case["case_id"],
            "profiles": {s: {k: v for k, v in asdict(p).items() if k != "notes"} for s, p in self.profiles.items()},
            "claims": {s: [asdict(c) for c in self.claims(s)] for s in self.case["suppliers"]},
        }


def dump(d: Dossier):
    return json.dumps(d.summary(), indent=1, ensure_ascii=False, default=str)
