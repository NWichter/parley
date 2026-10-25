"""Write Parley's e-mail for each picked situation with each writer model, through Parley's own
writer.compose (same prompt, same validation, same 12 s timeout, same template fallback).

    cd agent && uv run python ../eval/writer_ab/gen.py [--reps 3]
"""

import argparse
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from common import OUT, env

env("ANTHROPIC_API_KEY_2", "OPENROUTER_API_KEY")
import os  # noqa: E402

os.environ["ANTHROPIC_API_KEY"] = os.environ.pop("ANTHROPIC_API_KEY_2")

from parley import analyst, writer  # noqa: E402
from parley.llm import LLM  # noqa: E402
from parley.policy import Decision  # noqa: E402

MODELS = {
    "opus": "claude-opus-5-5",
    "sonnet": "claude-sonnet-5-5",
    "haiku": "claude-haiku-5-5",
    "gpt": "openrouter:openai/gpt-6.1-sol",
    "gemini": "openrouter:google/gemini-4-argon",
}
PICK = {  # candidate index -> situation label
    0: "opening, price increase",
    44: "opening, copper pass-through",
    5: "opening, RFQ",
    6: "after a hold (step announced), RFQ",
    10: "after a hold, RFQ",
    17: "after a hold, RFQ (3 bidders)",
    25: "after a counter, quality",
    29: "after a counter, capacity",
    31: "after a counter, renewal",
    4: "final round, price increase",
    42: "final round, raw material",
    23: "final offer accepted (mirror), bundling",
}
REF = re.compile(r"\(([^()]*:[^()]*)\)")


@dataclass
class C:
    sentence: str
    refs: list = field(default_factory=list)


def decision(x):
    body, m = x["buyer"]["body"], x["journal"].get("metrics") or {}
    lines = {}
    v = re.search(r"binding volume of (\d+) pieces a year over (\d+) months", body)
    if v:
        lines["volume"] = (int(v.group(1)), int(v.group(2)))
        r = re.search(r"demand forecast \((fc:[^)]+)\)", body)
        if r:
            lines["volume_ref"] = r.group(1)
    lines["pay_on_time"] = "agreed due date" in body
    lines["cash"] = "include faster payment" in body
    lines["mirror"] = "we take it as offered" in (x["journal"].get("reason") or "") or body.count("We confirm:") > 0
    lines["breakdown"] = "cost breakdown for this part" in body
    lines["ask_floor"] = "best price" in body
    claims = []
    for s in x["journal"].get("claims") or []:
        refs = [r.strip() for g in REF.findall(s) for r in g.split(",")]
        claims.append(C(s, refs))
    d = Decision("send", x["supplier"], sheets=[t["terms"] for t in x["buyer"]["term_sheets"]], claims=claims, lines=lines,
                 metrics={"moved": bool(m.get("moved"))})
    return d


class Rec(LLM):
    def __init__(self, model):
        super().__init__(write_model=model)
        self.raw = []

    def _call(self, model, role, system, user, max_tokens, effort, tag=None):
        t0 = time.monotonic()
        try:
            text = super()._call(model, role, system, user, max_tokens, effort, tag)
            self.raw.append({"text": text, "s": round(time.monotonic() - t0, 2), "err": None})
            return text
        except Exception as e:
            self.raw.append({"text": None, "s": round(time.monotonic() - t0, 2), "err": f"{type(e).__name__}: {str(e)[:200]}"})
            raise


def verdict(raw):
    t = (raw or "").strip().strip('"')
    if not t:
        return "empty"
    hit = writer.FORBIDDEN.search(t)
    if hit:
        return f"forbidden:{hit.group(0)}"
    if len(t.split()) > 90:
        return "too_long"
    if len(t) < 20:
        return "too_short"
    return "ok"


def one(label, model, x, rep):
    llm = Rec(model)
    sup = [mm for mm in x["history"] if mm.get("from") != "buyer"]
    last = sup[-1] if sup else None
    reading = analyst.read_reply(last) if last else None
    t0 = time.monotonic()
    msg, used = writer.compose({"title": x["title"], "type": x["type"], "negotiable": x["negotiable"]}, decision(x), last,
                               reading, x["first"], llm, None)
    wall = round(time.monotonic() - t0, 2)
    for _ in range(600):
        if llm.raw:
            break
        time.sleep(0.1)
    raw = llm.raw[0] if llm.raw else {"text": None, "s": None, "err": "no call"}
    s = llm.usage.summary()
    why = "ok" if used else ("timeout" if s["timeouts"] else (raw["err"] or verdict(raw["text"])))
    return {"label": label, "model": model, "rep": rep, "used_llm": used, "why": why, "wall_s": wall, "model_s": raw["s"],
            "raw": raw["text"], "err": raw["err"], "usd": s["usd"], "in": s["input_tokens"], "out": s["output_tokens"],
            "body": msg["body"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3)
    a = ap.parse_args()
    cands = json.loads((OUT / "candidates.json").read_text(encoding="utf-8"))
    sits = []
    for n, label in PICK.items():
        x = cands[n]
        sup = [mm for mm in x["history"] if mm.get("from") != "buyer"]
        sits.append({"id": len(sits) + 1, "cand": n, "label": label, "session": x["session"], "case_id": x["case_id"],
                     "type": x["type"], "title": x["title"], "round": x["round"], "supplier_mail": sup[-1]["body"],
                     "x": x})
    rows, lock = [], threading.Lock()

    def model_run(name):
        for rep in range(a.reps):
            for s in sits:
                r = one(s["label"], MODELS[name], s["x"], rep)
                r.update(sit=s["id"], name=name)
                with lock:
                    rows.append(r)
                print(name, rep, s["id"], r["why"], r["wall_s"], flush=True)

    with ThreadPoolExecutor(len(MODELS)) as pool:
        list(pool.map(model_run, MODELS))
    for s in sits:
        s.pop("x")
    (OUT / "situations.json").write_text(json.dumps(sits, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "mails.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
