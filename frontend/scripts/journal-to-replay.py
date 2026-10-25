"""Builds frontend/src/app/replay/journal/replay.json from one Parley decision journal.

Usage: python frontend/scripts/journal-to-replay.py [session]
The supplier's e-mail text (condensed) comes from the arena replay in frontend/src/app/replay/p11.json.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SESSION = sys.argv[1] if len(sys.argv) > 1 else "3c38a30d93a9bc06"
JOURNAL = ROOT / "agent" / "runs" / "journal" / f"{SESSION}.jsonl"
LISTEN = ROOT / "frontend" / "src" / "app" / "replay" / "p11.json"
OUT = ROOT / "frontend" / "src" / "app" / "replay" / "journal" / "replay.json"

REF = re.compile(r"\b[a-z]{2,4}:[A-Za-z0-9@.\-]+")


def refs_of(sentence):
    return [r.rstrip(".,") for r in REF.findall(sentence)]


def split_body(body):
    parts = [p.strip() for p in body.split("\n\n") if p.strip()]
    model = parts[1] if len(parts) > 2 else ""
    code = [line for p in parts[2:-1] for line in p.split("\n") if line.strip()]
    return model, code


def main():
    events = [
        json.loads(line)
        for line in JOURNAL.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    listen = json.loads(LISTEN.read_text(encoding="utf-8"))
    if listen["session_id"] != SESSION:
        sys.exit(f"{LISTEN.name} holds session {listen['session_id']}, not {SESSION}")

    sup_turns = [t for t in listen["turns"] if t["speaker"] == "supplier"]
    opening = sup_turns[0]
    dossier = next(e for e in events if e["event"] == "dossier")
    sid, profile = next(iter(dossier["profiles"].items()))
    bank = dossier["claims"][sid]
    result = next(e for e in events if e["event"] == "result")
    close = next(e for e in events if e["event"] == "close")

    sends = [e for e in events if e["event"] == "send"]
    replies = [e for e in events if e["event"] == "reply"]
    first_send = sends[0]["metrics"]

    rounds = []
    for i, (s, r) in enumerate(zip(sends, replies), start=1):
        m = s["metrics"]
        model_par, code_lines = split_body(s["message"]["body"])
        verdicts = [c["result"] for c in r.get("claim_results", [])]
        facts = [
            {
                "sentence": c,
                "refs": refs_of(c),
                "verdict": verdicts[k] if k < len(verdicts) else None,
            }
            for k, c in enumerate(s["claims"])
        ]
        alts = [a["terms"] for a in r["reading"].get("alternatives", [])]
        rounds.append(
            {
                "n": i,
                "reason": s["reason"],
                "writer": m.get("writer"),
                "c_standing": m["c_standing"],
                "supplier_value_standing": m.get("supplier_value_standing"),
                "analyst": m.get("analyst"),
                "flags_before": m.get("flags", []),
                "facts": facts,
                "asks": m["asks"],
                "model_paragraph": model_par,
                "code_lines": code_lines,
                "reply": {
                    "status": r["status"],
                    "terms": [r["terms"], *alts],
                    "flags": sorted(k for k, v in r["reading"]["flags"].items() if v),
                    "drivers_named": r["reading"].get("series_named", []),
                    "attachments": r["reading"].get("attachments", []),
                    "text": sup_turns[i]["text"] if i < len(sup_turns) else "",
                    "from": sup_turns[i]["from"] if i < len(sup_turns) else "",
                },
            }
        )

    out = {
        "session": SESSION,
        "case_id": result.get("case") or events[0]["case"],
        "label": "Parley v1 practice session, Sat 24 Oct",
        "agent": listen["agent"],
        "arena_replay": f"https://arena.tacto.ai/replay/{SESSION}",
        "house_score": listen["house_score"],
        "supplier": {
            "id": sid,
            "name": profile["name"],
            "cash_short": profile["cash_short"],
            "staff": profile["staff"],
        },
        "buyer": "Hollerau Pumpenwerke, Purchasing",
        "claim_bank": [
            {
                "kind": c["kind"],
                "refs": c["refs"],
                "sentence": c["sentence"],
                "strength": round(c["strength"], 2),
            }
            for c in bank
        ],
        "opening": {
            "terms": opening["terms"],
            "text": opening["text"],
            "from": opening["from"],
            "c": first_send["c_standing"],
            "c_alt": first_send["c_alt"],
            "must_haves": first_send.get("must_haves"),
        },
        "rounds": rounds,
        "close": {
            "action": close["action"],
            "reason": close["reason"],
            "c_deal": close["metrics"]["c_deal"],
        },
        "result": {
            k: result[k]
            for k in ("deal", "score", "c_open", "c_deal", "c_best", "c_alt", "terms")
        },
        "arena_rounds": listen["rounds"],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"{OUT.relative_to(ROOT)}: {len(rounds)} rounds, score {out['result']['score']:.3f}"
    )


if __name__ == "__main__":
    main()
