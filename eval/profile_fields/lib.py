import json, os, re, sys
from pathlib import Path
HERE = Path(__file__).parent
ROOT = Path(__file__).resolve().parents[2]

def package():
    for c in (os.environ.get("ARENA_DATA"), ROOT / "agent" / "data" / "package",
              ROOT / "internal" / "briefing" / "tacto" / "repo" / "data"):
        if c and (Path(c) / "data" / "suppliers.csv").is_file():
            return Path(c)
    sys.exit("no data package: run `parley data` or set ARENA_DATA")

B = json.load(open(HERE / "bundles.json", encoding="utf-8"))
PROMPT = (HERE / "prompt_v1.txt").read_text(encoding="utf-8")
SCALARS = ["approval_threshold_pct", "approval_cycle", "approval_cycle_days", "capacity_tight", "price_freeze",
           "cash_need", "uninformed_owner", "decision_maker", "patience", "past_goodwill_claim", "volume_posture",
           "open_calculation", "margin_floor", "account_deprioritised"]
LISTS = ["preferred_levers", "avoid_levers", "hedged_inputs"]
LEVERS = {"payment_days_short", "payment_days_long", "lead_time_slack", "contract_months_longer", "volume_commitment",
          "index_clause", "effective_date_later", "ppm_relaxed", "chargeback_none", "tooling"}

def render(sid):
    return "\n\n".join(f"[{u['id']}] ({u['kind']}{', ' + u['date'] if u['date'] else ''})\n{u['text']}" for u in B[sid]["units"])

def user_msg(sid):
    return f"Supplier {sid}: {B[sid]['name']}\n\nSOURCES\n\n{render(sid)}"

def full_prompt(sid):
    return PROMPT + "\n\n" + user_msg(sid)

def norm(s):
    s = s.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"').replace("\u2013", "-").replace("\u2014", "-")
    s = s.replace("*", "")
    return re.sub(r"\s+", " ", s).strip().lower()

def unit_text(sid, uid):
    for u in B[sid]["units"]:
        if u["id"] == uid:
            return u["text"]
    return None

def verify(sid, ev):
    t = unit_text(sid, ev.get("source", ""))
    q = ev.get("quote") or ""
    if t is None or len(q.strip()) < 4:
        return False
    nt, parts = norm(t), [norm(p) for p in re.split(r"\.\.\.|\u2026", q) if p.strip()]
    pos = 0
    for p in parts:
        i = nt.find(p, pos)
        if i < 0:
            return False
        pos = i + len(p)
    return bool(parts)

def parse_json(text):
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    i, j = text.find("{"), text.rfind("}")
    return json.loads(text[i:j + 1])

def env_key(name):
    for line in open(ROOT / ".env", encoding="utf-8"):
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
