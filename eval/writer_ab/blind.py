"""Shuffle and anonymise the five versions per situation (rep 0, as Parley would have sent it).
Writes out/packets/<n>.md (what judges see) and out/key.json (letter -> model; never shown to judges)."""

import json
import random

from common import OUT

LETTERS = "ABCDE"
CRITERIA = """Rate every version 1-10 on each criterion:
- credibility: honest; no invented statements attributed to the supplier, no claims about the past or
  the relationship that the supplier's e-mail does not contain, no empty promises ("we are happy to explain ...")
- clarity: easy to read, clear what the buyer wants
- tone: firm, respectful, not threatening, not servile
- persuasiveness: makes the supplier more likely to move or agree
- fit: fits the facts given (the supplier's e-mail and the case); reacts to what the supplier actually wrote
Also list every hallucination: an exact sentence (or part) of the version that states something about the
supplier's e-mail, the supplier, or the history that the supplier's e-mail does not say or clearly imply."""
FORMAT = """Answer with JSON only, no prose before or after:
{"situation": <n>, "ratings": {"A": {"credibility": 0, "clarity": 0, "tone": 0, "persuasiveness": 0, "fit": 0,
"hallucinations": ["..."]}, "B": {...}, "C": {...}, "D": {...}, "E": {...}}, "best": "<letter>", "worst": "<letter>"}"""


def packet(s, versions):
    out = [f"## Situation {s['id']}: {s['title']} ({s['type']}, round {s['round']}: {s['label']})", "",
           "### The supplier's last e-mail (what the buyer is answering)", "```", s["supplier_mail"].strip(), "```", ""]
    for L in LETTERS:
        out += [f"### Version {L}", "```", versions[L].strip(), "```", ""]
    return "\n".join(out)


def main():
    sits = json.loads((OUT / "situations.json").read_text(encoding="utf-8"))
    mails = json.loads((OUT / "mails.json").read_text(encoding="utf-8"))
    rng = random.Random(20261025)
    key, packets = {}, []
    (OUT / "packets").mkdir(exist_ok=True)
    for s in sits:
        rows = sorted([m for m in mails if m["sit"] == s["id"] and m["rep"] == 0], key=lambda m: m["name"])
        rng.shuffle(rows)
        key[s["id"]] = {L: r["name"] for L, r in zip(LETTERS, rows)}
        p = packet(s, {L: r["body"] for L, r in zip(LETTERS, rows)})
        (OUT / "packets" / f"{s['id']:02d}.md").write_text(p, encoding="utf-8")
        packets.append(p)
    (OUT / "key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
    head = ("You judge e-mails a buyer (Hollerau Pumpenwerke, Purchasing, a German pump maker) sends to an industrial "
            "supplier in a price negotiation. For each situation you get the supplier's last e-mail and five versions "
            "A-E of the buyer's reply. The figures, term sheets and data references are the same in all versions; the "
            "versions differ mainly in the opening paragraph, which a language model wrote. Some versions fall back to a "
            "short fixed opening. Judge each e-mail as a whole, as the recipient would read it.\n\n" + CRITERIA)
    (OUT / "packets" / "ALL.md").write_text(head + "\n\n" + FORMAT.replace('"situation": <n>, ', '"situation": <n>, ')
                                            + "\nFor all situations answer a JSON list of such objects.\n\n" + "\n\n".join(packets), encoding="utf-8")
    (OUT / "packets" / "HEAD.txt").write_text(head + "\n\n" + FORMAT, encoding="utf-8")


if __name__ == "__main__":
    main()
