"""Fetch the threads of real arena sessions (read-only GETs) and list every buyer e-mail Parley
sent, with the supplier e-mail it answered and the context the writer needs.

    cd agent && uv run python ../eval/writer_ab/collect.py <session_id> ...
"""

import json
import sys

from common import OUT, ROOT, env

env("ARENA_API_KEY")
from parley.kit.client import ArenaClient  # noqa: E402


def journal(sid):
    p = ROOT / "agent" / "runs" / "journal" / f"{sid}.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]


def main(sessions):
    c = ArenaClient()
    cases = {x["case_id"]: x for s in ("practice", "sandbox") for x in c.cases(s)}
    out = []
    for sid in sessions:
        ev = journal(sid)
        state = c.session(sid)
        case = cases[state["case_id"]]
        sends = [e for e in ev if e["event"] == "send"]
        for sup in state["threads"]:
            msgs = c.thread(sid, sup)["messages"]
            js = [e for e in sends if e["supplier"] == sup]
            k = 0
            for i, m in enumerate(msgs):
                if m.get("from") != "buyer" or m.get("action"):
                    continue
                prior = msgs[:i]
                sup_msgs = [x for x in prior if x.get("from") != "buyer"]
                j = js[k] if k < len(js) else {}
                k += 1
                out.append({
                    "session": sid, "case_id": case["case_id"], "type": case["type"], "title": case["title"],
                    "negotiable": case["negotiable"], "supplier": sup, "round": k,
                    "first": not any(x.get("from") == "buyer" for x in prior),
                    "history": prior, "buyer": m, "journal": {kk: j.get(kk) for kk in ("reason", "metrics", "claims")},
                    "next": msgs[i + 1] if i + 1 < len(msgs) else None,
                    "supplier_status": sup_msgs[-1].get("status") if sup_msgs else None,
                })
    OUT.mkdir(exist_ok=True)
    (OUT / "candidates.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for n, x in enumerate(out):
        a = (x["journal"].get("metrics") or {}).get("analyst") or {}
        print(n, x["session"], x["case_id"], x["type"], x["supplier"], "r", x["round"], x["supplier_status"],
              "moved" if (x["journal"].get("metrics") or {}).get("moved") else "-", a.get("tactic"), (x["journal"].get("reason") or "")[:70])


if __name__ == "__main__":
    main(sys.argv[1:])
