"""08:15 offline reader check: the rule reader (analyst.read_reply) against the model reader
(analyst.classify, ANALYST_MODEL) over the Sunday release texts and the supplier mails of our recent
ended practice/sandbox sessions. Reads only GET /v1/sessions, GET /v1/sessions/{sid} and its threads
(no quota). Prints, per signal, how often the model sees it where the rules do not, with examples,
and writes the full table to ops/logs/<date>/reader-check-<time>.json.

Run from agent/: uv run --frozen python ../ops/reader_check.py [--release DIR] [--sessions 15]"""

import argparse
import csv
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

OPS = Path(__file__).resolve().parent
AGENT = OPS.parent / "agent"
sys.path.insert(0, str(AGENT))

from parley import llm as llm_mod  # noqa: E402
from parley.analyst import SIGNALS_MAP, classify, read_reply  # noqa: E402
from parley.cli import load_env  # noqa: E402
from parley.dossier import default_data_dir  # noqa: E402
from parley.kit.client import ArenaClient, ArenaError  # noqa: E402


def release_dir(explicit):
    if explicit:
        return Path(explicit)
    pkg = default_data_dir()
    for p in ((pkg / "release-sunday") if pkg else None, (pkg.parent / "release-sunday") if pkg else None,
              AGENT / "data" / "release-sunday"):  # fmt: skip
        if p and (p / "facts.json").is_file():
            return p
    return None


def release_texts(rel):
    out = []
    for p in sorted((rel / "correspondence").glob("*.md")):
        out.append({"source": f"release:{p.name}", "body": p.read_text(encoding="utf-8")})
    news = rel / "data" / "news.csv"
    if news.is_file():
        for r in csv.DictReader(news.open(encoding="utf-8")):
            out.append({"source": f"release:{r['id']}", "body": f"{r['headline']}. {r['body']}"})
    return out


def session_mails(c, n):
    rows = []
    for set_ in ("practice", "sandbox"):
        try:
            got = c.sessions(set_)
        except ArenaError as e:
            print(f"sessions {set_}: {e}")
            continue
        got = got.get("sessions", got) if isinstance(got, dict) else got
        rows += [s for s in got if s.get("status") != "open"]
    rows.sort(key=lambda s: s.get("created_at") or 0, reverse=True)
    out = []
    for s in rows[:n]:
        sid = s["session_id"]
        try:
            threads = c.session(sid).get("threads") or {}
            for sup in threads:
                for m in c.thread(sid, sup).get("messages") or []:
                    if m.get("from") != "buyer" and m.get("body"):
                        out.append({"source": f"{s.get('case_id')}/{sid}/{sup}", "body": m["body"]})
                time.sleep(0.3)
        except ArenaError as e:
            print(f"{sid}: {e}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--release", help="release folder (default: next to the data package)")
    ap.add_argument("--sessions", type=int, default=15, help="recent ended sessions to read (0: none)")
    a = ap.parse_args()
    load_env()
    llm = llm_mod.from_env()
    if llm is None:
        sys.exit("no model: ANTHROPIC_API_KEY missing")
    texts = []
    rel = release_dir(a.release)
    if rel:
        texts += release_texts(rel)
        print(f"release: {rel} ({len(texts)} texts)")
    else:
        print("release: none found")
    if a.sessions:
        mails = session_mails(ArenaClient(), a.sessions)
        print(f"supplier mails: {len(mails)} from the last {a.sessions} ended sessions")
        texts += mails
    rows, missed, extra = [], Counter(), Counter()
    examples = {}
    t0 = time.time()
    for t in texts:
        rules = read_reply({"body": t["body"]}).flags
        model = classify(llm, {"body": t["body"]}, timeout=20) or {}
        row = {"source": t["source"], "rules": sorted(rules), "model": {k: v for k, v in model.items() if v not in (False, None)}}
        for key, flag in SIGNALS_MAP.items():
            if model.get(key) is True and not rules.get(flag):
                missed[flag] += 1
                examples.setdefault(flag, []).append(t["source"])
            if rules.get(flag) and model.get(key) is False:
                extra[flag] += 1
        rows.append(row)
    print(f"\n{len(texts)} texts in {time.time() - t0:.0f} s; llm {json.dumps(llm.usage.summary())}")
    print("\nsignal          model only  rules only  examples (model only)")
    for flag in sorted(set(missed) | set(extra)):
        print(f"{flag:<15} {missed[flag]:>10}  {extra[flag]:>10}  {', '.join(examples.get(flag, [])[:4])}")
    if not missed:
        print("the model found no signal the rules miss: the final stays --no-llm")
    out = OPS / "logs" / datetime.now().strftime("%Y-%m-%d")
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"reader-check-{datetime.now():%H%M%S}.json"
    path.write_text(json.dumps({"missed": missed, "extra": extra, "rows": rows}, indent=1, ensure_ascii=False),
                    encoding="utf-8")  # fmt: skip
    print(f"\nfull table: {path}")


if __name__ == "__main__":
    main()
