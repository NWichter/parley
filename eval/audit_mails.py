# /// script
# requires-python = ">=3.12"
# dependencies = ["anthropic>=0.70", "httpx>=0.27"]
# ///
"""Audit the buyer's e-mails in a bank run's transcripts.

    uv run eval/audit_mails.py eval/results/<ts> [--agent llm] [--judge 60] [--model claude-sonnet-5-5]
    uv run eval/audit_mails.py eval/results/<ts> --lint [--agent cand]    # exit 1 on any lint failure

--lint is the blocking mail lint (also run by agent/tests/test_mail_lint.py): banned phrases, repeated
openings, a missing contact name, statements the supplier's mail does not back, sentence limits.

Code checks every buyer e-mail: the greeting line, length, sentences repeated across rounds of the
same thread. With --judge N, a model (key ANTHROPIC_API_KEY_1) reads N buyer e-mails with the supplier
e-mail before each and lists sentences that attribute to the supplier something it did not write, or
promise something the buyer cannot keep.
"""

import argparse
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "agent"))

MSG = re.compile(r"### (BUYER|SUPPLIER \[\w+\])\n(?:action: .*\n)?```\n(.*?)```", re.S)
BAD_GREETING = re.compile(r"^Dear (Sir or Madam|.*\b(regards|team|GmbH|KG|AG|purchasing|Mr\.?\s*$)\b.*),$", re.I)
JUDGE = """You check a buyer's e-mail in a supplier negotiation. You get the supplier's last e-mail and the
buyer's reply. List every sentence of the buyer that
(a) attributes to the supplier something the supplier's e-mail does not say or clearly imply
    (an offer, a statement, a confirmation, a feeling), or
(b) promises an action the buyer does not then take in the same e-mail (e.g. "we will explain our sources"
    with no explanation following).
Answer JSON only: {"fabricated": ["<exact sentence>", ...], "empty_promises": ["<exact sentence>", ...],
"padding_sentences": <number of sentences that carry no argument, offer or question>}"""


def threads(path):
    """[(game, [(who, body), ...]), ...] per supplier thread."""
    out = []
    for f in sorted(Path(path, "transcripts").glob("*.md")):
        for part in re.split(r"\n## ", f.read_text(encoding="utf-8"))[1:]:
            out.append((f.stem, [(w.split()[0], b.strip()) for w, b in MSG.findall(part)]))
    return out


# -- the blocking mail lint ---------------------------------------------------------------------------

BLOCK = re.compile(r"^### (BUYER|SUPPLIER \[(\w+)\])\n(?:action: .*\n)?```\n(.*?)```\n((?:- [^\n]*\n?)*)", re.S | re.M)
BANNED = re.compile(
    r"\btoday\b|\btomorrow\b|by end of|this week|\bchoose\b|equally well|either way|another (quote|offer)|"
    r"other (suppliers|offers)|competitor|in due course|come back to you|\brevert\b|get back to you|"
    r"we understand your (cost|price)|justified|reasonable increase|checked internally|look forward to hearing|!",
    re.I,
)
UNVERIFIABLE = re.compile(
    r"as you (said|mentioned|told)|goodwill|history|your (earlier|previous|prompt)|you (agreed|promised|offered to)|"
    r"we understand (that|your)|we will explain|happy to (explain|share)|in the past|last time",
    re.I,
)
LEADIN = re.compile(r"^(Here are|We have set out|Below are) \d+ packages\b.*:$")
REF_IN_TEXT = re.compile(r"\([a-z]+:[^)\s]+")
SENT = re.compile(r"(?<=[.!?])\s+")


def parse_threads(path, agent=None):
    """{game: [[msg, ...] per supplier thread]}, msg = {who, status, body, sheets, refs}."""
    out = {}
    for f in sorted(Path(path, "transcripts").glob("*.md")):
        if agent and not f.stem.startswith(agent + "__"):
            continue
        ths = []
        for part in re.split(r"\n## ", f.read_text(encoding="utf-8"))[1:]:
            msgs = []
            for who, status, body, meta in BLOCK.findall(part):
                sheets, refs = [], []
                for line in meta.splitlines():
                    if line.startswith("- sheet: "):
                        try:
                            sheets.append(json.loads(line[9:]))
                        except ValueError:
                            pass
                    elif line.startswith("- refs: "):
                        refs = [r.strip() for r in line[8:].split(",") if r.strip()]
                msgs.append({"who": who.split()[0], "status": status or "", "body": body.strip(), "sheets": sheets, "refs": refs})
            ths.append(msgs)
        out[f.stem] = ths
    return out


def split_mail(body):
    """(greeting, opening, data block, lead-in, closing, sign-off) of one of our mails."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", body.strip()) if p.strip()]
    greeting = paras[0] if paras else ""
    opening = paras[1] if len(paras) > 1 else ""
    sign = "\n".join(paras[-1:]) if len(paras) > 2 else ""
    closing = paras[-2] if len(paras) > 3 else ""
    middle = "\n".join(paras[2:-2]).splitlines()
    lead = [x for x in middle if LEADIN.match(x)]
    block = "\n".join(x for x in middle if not LEADIN.match(x))
    return greeting, opening, block, (lead[0] if lead else ""), closing, sign


def data_block(body):
    return split_mail(body)[2]


def _sentences(text):
    return [x for x in SENT.split(text.strip()) if x.strip()] if text.strip() else []


def _price(msg):
    return (msg["sheets"][0] if msg["sheets"] else {}).get("unit_price")


def lint_thread(msgs):
    """Lint failures [(rule, detail), ...] for our mails in one supplier thread."""
    from parley import analyst, writer

    sit_of = {}
    for sit, variants in writer.OPENINGS.items():
        for v in variants:
            for pk in ("one of the packages below", "the package below"):
                for th in ("Thank you for your revised offer.", "Thank you for your reply."):
                    sit_of[writer._norm(v.format(pk=pk, thanks=th))] = sit
    fails, seen, sups = [], set(), []
    for i, m in enumerate(msgs):
        if m["who"] != "BUYER":
            sups.append(m)
            continue
        greet, opening, block, lead, closing, sign = split_mail(m["body"])
        want = writer.greeting_from(sups)
        if greet != want:
            fails.append(("greeting", f"{greet!r} instead of {want!r}"))
        if sign != writer.SIGN_OFF:
            fails.append(("sign_off", sign[:60]))
        prose = " ".join((opening, lead, closing))
        for rx, rule in ((BANNED, "banned"), (UNVERIFIABLE, "unverifiable")):
            hit = rx.search(prose)
            if hit:
                fails.append((rule, hit.group(0)))
        if writer.FORBIDDEN.search(opening):
            fails.append(("forbidden_in_opening", writer.FORBIDDEN.search(opening).group(0)))
        if len(opening.split()) > 30:
            fails.append(("opening_long", opening))
        if len(_sentences(opening)) + len(_sentences(closing)) > 3:
            fails.append(("too_many_sentences", f"{opening} | {closing}"))
        key = writer._norm(opening)
        if key in seen:
            fails.append(("repeated_opening", opening))
        seen.add(key)
        if sum(1 for x in block.splitlines() if re.match(r"\d+\. ", x)) >= 2 and not lead:
            fails.append(("lead_in_missing", block[:80]))
        sit = sit_of.get(key)
        last = sups[-1] if sups else None
        prev = sups[-2] if len(sups) >= 2 else None
        changed = bool(last and prev and last["sheets"][:1] != prev["sheets"][:1])
        backed = {
            "conceded": changed and (_price(last) or 0) <= (_price(prev) or 0),
            "revised": changed,
            "step_announced": bool(last and analyst.SIGNALS["step_announced"].search(last["body"])),
            "data_request": bool(last and analyst.SIGNALS["wants_data"].search(last["body"]) and REF_IN_TEXT.search(block)),
            "accept": "We confirm:" in block,
        }
        if sit and sit.startswith("first") and any(x["who"] == "BUYER" for x in msgs[:i]):
            fails.append(("unverifiable", f"first-mail opening later in the thread: {opening}"))
        if sit in backed and not backed[sit]:
            fails.append(("unverifiable", f"{sit} opening without basis in the supplier's mail: {opening}"))
        if "data below" in opening and not REF_IN_TEXT.search(block):
            fails.append(("unverifiable", f"refers to data that the mail does not carry: {opening}"))
    return fails


def lint(run, agent=None):
    """{game: [(rule, detail), ...]} for every game with a failure; {} is a pass."""
    out = {}
    for game, ths in parse_threads(run, agent).items():
        fails = [f for msgs in ths for f in lint_thread(msgs)]
        if fails:
            out[game] = fails
    return out


def mail_count(run, agent=None):
    return sum(1 for ths in parse_threads(run, agent).values() for msgs in ths for m in msgs if m["who"] == "BUYER")


def paired_blocks(run, a, b):
    """Games of agents a and b (same persona, case, seed) whose data blocks, sheets or refs differ."""
    ga, gb = parse_threads(run, a), parse_threads(run, b)
    diffs = {}
    for game, ths in ga.items():
        other = gb.get(b + game[len(a):])
        if other is None:
            continue

        def sig(t):
            return [[(data_block(m["body"]), m["sheets"], m["refs"]) for m in msgs if m["who"] == "BUYER"] for msgs in t]

        if sig(ths) != sig(other):
            diffs[game] = (sig(ths), sig(other))
    return diffs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--agent", help="only games of this agent label")
    ap.add_argument("--judge", type=int, default=0, help="buyer e-mails for the model to check")
    ap.add_argument("--model", default="claude-sonnet-5-5")
    ap.add_argument("--out")
    ap.add_argument("--lint", action="store_true", help="blocking lint: list failures, exit 1 if any")
    a = ap.parse_args()
    if a.lint:
        res = lint(a.run, a.agent)
        n = mail_count(a.run, a.agent)
        bad = sum(len(v) for v in res.values())
        print(f"mail lint: {n} buyer e-mails, {bad} failure(s) in {len(res)} game(s)")
        for game, fails in sorted(res.items())[:40]:
            for rule, detail in fails[:5]:
                print(f"- {game}: {rule}: {detail}")
        sys.exit(1 if res or not n else 0)
    ths = [t for t in threads(a.run) if not a.agent or t[0].startswith(a.agent + "__")]
    greet, words, repeats, pairs = Counter(), [], Counter(), []
    for game, msgs in ths:
        seen = Counter()
        for i, (who, body) in enumerate(msgs):
            if who != "BUYER":
                continue
            first = next((x.strip() for x in body.splitlines() if x.strip()), "")
            if BAD_GREETING.match(first):
                greet[first] += 1
            words.append(len(body.split()))
            for s in re.split(r"(?<=[.!?])\s+", body):
                s = s.strip()
                if len(s) > 40 and not re.search(r"\d", s):
                    seen[s] += 1
            if i > 0 and msgs[i - 1][0] == "SUPPLIER":
                pairs.append((game, msgs[i - 1][1], body))
        for s, n in seen.items():
            if n > 1:
                repeats[s] += n - 1
    n = len(words)
    md = [f"# Buyer e-mail audit: {Path(a.run).name}" + (f" (agent {a.agent})" if a.agent else ""), "",
          f"{n} buyer e-mails in {len(ths)} threads; mean {sum(words) / max(n, 1):.0f} words (max {max(words or [0])}).", "",
          "## Greeting lines that do not name a person", ""]  # fmt: skip
    md += [f"- ({c}x) `{g}`" for g, c in greet.most_common(10)] or ["- none"]
    md += ["", "## Sentences repeated within one thread (no figures), most often", ""]
    md += [f"- ({c}x) {s}" for s, c in repeats.most_common(10)] or ["- none"]
    if a.judge:
        from supplier_llm import SupplierLLM, load_key

        llm = SupplierLLM(
            {"anthropic": load_key(HERE.parent, "ANTHROPIC_API_KEY_1")}, a.model, budgets={"anthropic": 2.0}
        )
        random.seed(7)
        sample = random.sample(pairs, min(a.judge, len(pairs)))
        fab, prom, pad = [], [], []
        for game, sup, buyer in sample:
            try:
                text = llm.call(
                    "writer",
                    JUDGE,
                    f"SUPPLIER:\n<<<\n{sup[:3500]}\n>>>\n\nBUYER:\n<<<\n{buyer[:3500]}\n>>>",
                    "audit",
                    800,
                )
                d = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
            except Exception:  # noqa: BLE001
                continue
            fab += [(game, s) for s in d.get("fabricated") or []]
            prom += [(game, s) for s in d.get("empty_promises") or []]
            pad.append(d.get("padding_sentences") or 0)
        k = max(len(pad), 1)
        md += ["", f"## Model check of {len(pad)} buyer e-mails ({a.model}, ${llm.spent():.3f})", "",
               f"E-mails with a fabricated attribution: {len({g for g, _ in fab})} ({len(fab)} sentences); "
               f"with an empty promise: {len({g for g, _ in prom})}; padding sentences per e-mail: {sum(pad) / k:.1f}.", "",
               "Fabricated attributions (sample):", ""]  # fmt: skip
        md += [f'- `{g}`: "{s}"' for g, s in fab[:12]] or ["- none"]
        md += ["", "Empty promises (sample):", ""] + ([f'- `{g}`: "{s}"' for g, s in prom[:8]] or ["- none"])
    text = "\n".join(md) + "\n"
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
