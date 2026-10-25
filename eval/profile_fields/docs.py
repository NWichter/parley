"""Write profile-fields(.en).md from profile_fields.json + stats.json + doc_*.md templates.
Target folder: first argument, else the research notes folder."""

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "internal" / "research"
d = json.loads((ROOT / "agent/cache/profile_fields.json").read_text(encoding="utf-8"))
st = json.loads((HERE / "stats.json").read_text(encoding="utf-8"))
meta = d["_meta"]
sup = {k: v for k, v in d.items() if k != "_meta"}
NEVER = [
    "sup:70150",
    "sup:70170",
    "sup:70220",
    "sup:70230",
    "sup:70310",
    "sup:70420",
    "sup:70430",
    "sup:70550",
    "sup:70560",
]
cost = (
    json.loads((HERE / "cost.json").read_text(encoding="utf-8"))
    if (HERE / "cost.json").exists()
    else {}
)


def short(v):
    if isinstance(v, dict):
        return "freeze" + (f" until {v.get('until')}" if v.get("until") else "")
    if isinstance(v, list):
        return ", ".join(
            x
            if isinstance(x, str)
            else f"{x['input']}→{'/'.join(x['series'])}"
            + (f" until {x['until']}" if x.get("until") else "")
            for x in v
        )
    return str(v)


def stats_table(de):
    h = (
        "| Feld | Claude = Gemini | beide gleich und nicht null / mind. einer nicht null | Codex-Stichprobe (10): C=X / G=X / alle drei |"
        if de
        else "| Field | Claude = Gemini | both equal and non-null / at least one non-null | Codex sample (10): C=X / G=X / all three |"
    )
    rows = [h, "|---|---|---|---|"]
    for f, r in st["fields"].items():
        rows.append(
            f"| `{f}` | {r['agree']}/{r['n']} | {r['agree_nonnull']}/{r['any_nonnull']} | "
            f"{r['cx']}/{r['n3']} / {r['gx']}/{r['n3']} / {r['all3']}/{r['n3']} |"
        )
    return "\n".join(rows)


def low(s, f):
    x = s["fields"][f]
    if isinstance(x, list):
        return any(i["confidence"] == "low" for i in x)
    return x["confidence"] == "low"


def count_table(de):
    rows = [
        "| Feld | Lieferanten mit Wert | davon Konfidenz low |"
        if de
        else "| Field | suppliers with a value | of which confidence low |",
        "|---|---|---|",
    ]
    for f in next(iter(sup.values()))["summary"]:
        have = [s for s in sup.values() if s["summary"][f] not in (None, [])]
        rows.append(f"| `{f}` | {len(have)} | {sum(1 for s in have if low(s, f))} |")
    return "\n".join(rows)


COLS = [
    "approval_threshold_pct",
    "price_freeze",
    "cash_need",
    "capacity_tight",
    "hedged_inputs",
    "uninformed_owner",
    "decision_maker",
    "volume_posture",
    "preferred_levers",
    "avoid_levers",
]
OTHER = [
    "past_goodwill_claim",
    "open_calculation",
    "margin_floor",
    "account_deprioritised",
    "patience",
]


def table(ids):
    rows = [
        "| Supplier | " + " | ".join(f"`{c}`" for c in COLS) + " | other |",
        "|" + "---|" * (len(COLS) + 2),
    ]
    for sid in ids:
        s = sup[sid]["summary"]
        other = [f for f in OTHER if s[f] not in (None, [])]
        cells = [
            (short(s[c]) + (" (low)" if low(sup[sid], c) else ""))
            if s[c] not in (None, [])
            else ""
            for c in COLS
        ]
        rows.append(
            f"| {sid[4:]} {sup[sid]['name'][:30]} | "
            + " | ".join(cells)
            + f" | {', '.join(other)} |"
        )
    return "\n".join(rows)


items = "; ".join(f"`{f}` {i}/{u}" for f, (i, u) in st["items"].items())
q = st.get("quotes", {})
for lang, fn in (("en", "profile-fields.en.md"), ("de", "profile-fields.md")):
    de = lang == "de"
    tpl = (HERE / f"doc_{lang}.md").read_text(encoding="utf-8")
    status = (
        ""
        if meta.get("complete", True)
        else (
            ("**Teilstand:** " if de else "**Partial:** ")
            + meta.get("status_" + lang, "")
        )
    )
    qs = (
        (
            f"{q['verified']}/{q['total']} Zitate aller Modelle wörtlich in ihrer Quelle gefunden"
            if de
            else f"{q['verified']}/{q['total']} quotes of all models found verbatim in their source"
        )
        if q
        else "-"
    )
    text = (
        tpl.replace("{created}", meta["created"])
        .replace("{n}", str(len(sup)))
        .replace("{complete}", str(meta.get("complete", True)).lower())
        .replace("{status}", status)
        .replace("{sha}", meta["prompt_sha256_12"])
        .replace("{stats}", stats_table(de))
        .replace("{items}", items)
        .replace("{counts}", count_table(de))
        .replace("{never}", table(NEVER))
        .replace("{all}", table(sorted(sup)))
        .replace("{quotes}", qs)
        .replace("{cost}", cost.get(lang, "-"))
    )
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / fn).write_text(text, encoding="utf-8")
print("docs written")
