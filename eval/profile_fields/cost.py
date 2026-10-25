import json
from pathlib import Path
H = Path(__file__).parent
ti = to = 0
for d in ("claude", "adjud"):
    for f in (H / "out" / d).glob("*.json"):
        u = json.loads(f.read_text(encoding="utf-8"))["usage"]; ti += u["in"]; to += u["out"]
usd = ti * 4e-6 + to * 20e-6
json.dump({"en": f"Claude Opus 5.5 extraction + adjudication {ti:,} input / {to:,} output tokens = about ${usd:.2f} (list price $4/$20 per MTok). Gemini (agy) and Codex ran on the CLI subscriptions, no metered cost",
           "de": f"Claude Opus 5.5 Extraktion + Entscheidung {ti:,} Eingabe- / {to:,} Ausgabe-Tokens = etwa {usd:.2f} $ (Listenpreis 4/20 $ je MTok). Gemini (agy) und Codex liefen über die CLI-Abos, ohne gemessene Kosten".replace(",", ".")},
          open(H / "cost.json", "w", encoding="utf-8"), ensure_ascii=False)
print(ti, to, usd)
