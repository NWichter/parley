"""Strength guard: replay our best live threads with the build under test.

Every buyer turn of each recorded thread in eval/guards/threads/ is decided again with the current
code and the current environment (PARLEY_* flags), and the mail is composed again with the template
writer. The arena answers a thread with the same content the same way, so a build that sends the
same mails in every round gets the same score. Per thread:

  SAME    every mail (sheets, refs, text) and the close are the same as recorded -> same score
  TEXT    the first difference is in the text or refs only, the sheets are the same -> live check
  LOWER   the first different mail asks for sheets that cost us no more (published cost C) -> live check
  WORSE   a sheet costs us more, an accepted deal is not taken, or we walk / accept earlier at a
          higher cost -> blocks the merge
  ERROR   the decision fails

Exit code: 0 all SAME, 2 some TEXT/LOWER (run one live session per listed case before merging),
1 any WORSE/ERROR.

usage (repo root):
  ARENA_DATA=<kit data> uv run --project agent python eval/guards/strengths.py [--env K=V ...] [--json out.json]
"""

import argparse
import glob
import json
import math
import os
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def setup_env(pairs):
    for p in pairs or []:
        k, _, v = p.partition("=")
        os.environ[k] = v
    if not os.environ.get("ARENA_DATA"):
        for cand in (ROOT / "internal" / "briefing" / "tacto" / "repo" / "data",):
            if cand.is_dir():
                os.environ["ARENA_DATA"] = str(cand)
    for k in (
        "LLM_MODEL",
        "LLM_API_KEY",
        "LLM_PROVIDER",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        os.environ.pop(
            k, None
        )  # the guard checks the no-model path, the one that plays


def published_cost(case, terms, std):
    from parley.kit.cost import total_cost

    try:
        return total_cost(case, terms, std)
    except Exception:  # noqa: BLE001
        return math.inf


def sheets_of(msg):
    return [s.get("terms") or {} for s in (msg or {}).get("term_sheets") or []]


def compose(case, pol, sid, d, snapshot):
    """The runner's write(), without the client and the model."""
    from parley import runner

    fake = SimpleNamespace(
        policy=pol,
        personas={},
        case_id=case["case_id"],
        case=case,
        llm=None,
        mode="nollm",
    )
    return runner.CaseRunner.write(fake, sid, d, snapshot)


def replay(path):
    from parley.dossier import Dossier, apply_persona, load_package
    from parley.kit.docs import standard_terms
    from parley.policy import CasePolicy
    from parley.runner import load_personas

    rec = json.loads(Path(path).read_text(encoding="utf-8"))
    case, full = rec["case"], rec["threads"]
    pkg = load_package()
    if pkg is None:
        return {
            "file": Path(path).name,
            "case": case["case_id"],
            "verdict": "ERROR",
            "detail": "no data package (ARENA_DATA)",
        }
    from parley import rules
    from parley.dossier import _profile_fields_file

    rules.bind(rules.derive(case, _profile_fields_file()))
    dossier = Dossier(case, pkg)
    for sid, persona in load_personas(pkg).items():
        if sid in dossier.profiles and persona:
            apply_persona(dossier.profiles[sid], persona)
    pol = CasePolicy(case, dossier)
    turns = []
    for sup, ms in full.items():
        for j, m in enumerate(ms):
            if m.get("from") == "buyer":
                turns.append((j // 2, sup, j))
    turns.sort()
    out = {
        "file": Path(path).name,
        "case": case["case_id"],
        "session": rec["session"],
        "score": rec["result"]["score"],
        "record_env": rec.get("record_env") or {},
        "rounds": [],
    }
    deal_c = published_cost(case, rec["result"]["terms"], None)
    for t, sup, j in turns:
        snap = {
            s2: (ms[:j] if s2 == sup else ms[: 2 * t + 1]) for s2, ms in full.items()
        }
        recorded = full[sup][j]
        std = standard_terms(snap[sup])
        row = {"round": t, "supplier": sup}
        try:
            d = pol.decide(sup, snap)
            if d.kind == "done" and not pol.rfq:
                d = pol.close(snap, d.reason, time_left=False)
        except Exception as e:  # noqa: BLE001
            row.update(verdict="ERROR", detail=repr(e))
            out["rounds"].append(row)
            break
        row["kind"], row["reason"] = d.kind, d.reason[:160]
        rec_action = recorded.get("action") or {}
        if rec_action:
            if d.kind == rec_action.get("type") and (
                d.term_sheet_id or None
            ) == rec_action.get("term_sheet_id"):
                row["verdict"] = "SAME"
            else:
                row.update(
                    verdict="WORSE",
                    detail=f"recorded {rec_action}, now {d.kind} {d.term_sheet_id or ''}",
                )
            out["rounds"].append(row)
            if row["verdict"] != "SAME":
                break
            continue
        if d.kind != "send":
            if d.kind == "accept":
                acc = next(
                    (
                        s.get("terms")
                        for m in snap[sup]
                        for s in (m.get("term_sheets") or [])
                        if s.get("term_sheet_id") == d.term_sheet_id
                    ),
                    None,
                )
                c = published_cost(case, acc, std) if acc else math.inf
                row.update(
                    verdict="WORSE" if c > deal_c + 1 else "LOWER",
                    detail=f"accepts {d.term_sheet_id} early, C {c:,.0f} vs recorded deal {deal_c:,.0f}",
                )
            else:
                row.update(
                    verdict="WORSE", detail=f"{d.kind} instead of a counter-offer"
                )
            out["rounds"].append(row)
            break
        msg = compose(case, pol, sup, d, snap)
        new, old = sheets_of(msg), sheets_of(recorded)
        if new == old:
            same_text = msg.get("body") == recorded.get("body") and list(
                msg.get("refs") or []
            ) == list(recorded.get("refs") or [])
            row["verdict"] = "SAME" if same_text else "TEXT"
            if not same_text:
                row["detail"] = first_line_diff(
                    recorded.get("body") or "", msg.get("body") or ""
                )
        else:
            cn = [published_cost(case, s, std) for s in new]
            co = [published_cost(case, s, std) for s in old]
            worse = (max(cn) > max(co) + 1) or (min(cn) > min(co) + 1)
            row.update(
                verdict="WORSE" if worse else "LOWER",
                detail=f"sheets {brief(old)} -> {brief(new)}; C max {max(co):,.0f} -> {max(cn):,.0f}, min {min(co):,.0f} -> {min(cn):,.0f}",
            )
        out["rounds"].append(row)
        if row["verdict"] != "SAME":
            break
    verdicts = [r["verdict"] for r in out["rounds"]]
    out["verdict"] = next(
        (v for v in ("ERROR", "WORSE", "LOWER", "TEXT") if v in verdicts), "SAME"
    )
    first = next((r for r in out["rounds"] if r["verdict"] != "SAME"), None)
    out["detail"] = (
        f"round {first['round']}: {first.get('detail', '')}"
        if first
        else f"all {len(verdicts)} turns as recorded"
    )
    return out


def brief(sheets):
    keys = (
        "unit_price",
        "payment_days",
        "contract_months",
        "volume_per_year",
        "effective_date",
        "lead_time_days",
    )
    return " | ".join("/".join(str(s[k]) for k in keys if k in s) for s in sheets)


def first_line_diff(a, b):
    la, lb = a.splitlines(), b.splitlines()
    for x, y in zip(la, lb):
        if x != y:
            return f"text: '{x[:80]}' -> '{y[:80]}'"
    return f"text: {len(la)} -> {len(lb)} lines"


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--env",
        action="append",
        help="K=V set before the replay (repeatable), e.g. PARLEY_CASH_RATE=0.10",
    )
    ap.add_argument("--threads", default=str(HERE / "threads" / "*.json"))
    ap.add_argument("--json", help="write the per-round results here")
    a = ap.parse_args(argv)
    setup_env(a.env)
    import parley

    print(f"code: {list(parley.__path__)[0]} (the --project the guard runs in)")
    shown = {k: v for k, v in sorted(os.environ.items()) if k.startswith("PARLEY_")}
    print(f"env: {shown or 'no PARLEY_* set (code defaults)'}")
    res = [replay(p) for p in sorted(glob.glob(a.threads))]
    for r in res:
        if r["verdict"] in ("SAME", "ERROR"):
            continue
        for k in sorted(glob.glob(str(HERE / "known" / f"{r['case'][5:]}-*.json"))):
            kr = replay(k)
            if kr["verdict"] == "SAME":
                worse = kr["score"] < r["score"] - 0.0005
                r["verdict"] = "WORSE" if worse else "SAME"
                r["detail"] = (
                    f"replays known session {kr['session']} exactly, score {kr['score']:.4f}"
                )
                break
    for r in res:
        miss = {
            k: v for k, v in r.get("record_env", {}).items() if os.environ.get(k) != v
        }
        note = (
            f"  (record played with {miss})" if miss and r["verdict"] != "SAME" else ""
        )
        print(
            f"{r['verdict']:6} {r['case']} {r.get('score', 0):.4f} {r.get('session', '')}  {r['detail']}{note}"
        )
    if a.json:
        Path(a.json).write_text(
            json.dumps(res, indent=1, default=str), encoding="utf-8"
        )
    vs = {r["verdict"] for r in res}
    if vs & {"WORSE", "ERROR"}:
        print("FAIL: a strong case sends worse terms than its best thread")
        return 1
    if vs & {"TEXT", "LOWER"}:
        print("CHECK LIVE: one practice session per TEXT/LOWER case before the merge")
        return 2
    print("PASS: every strong case replays its best thread exactly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
