import json
b = json.load(open("bundles.json", encoding="utf-8"))
P = json.load(open(str(Path(__file__).resolve().parents[2] / "agent/cache/personas.json"), encoding="utf-8"))
def render(sid):
    return "\n".join(f"[{u['id']}] ({u['kind']}{', ' + u['date'] if u['date'] else ''})\n{u['text']}" for u in b[sid]["units"])
out = [open("schema_draft.md", encoding="utf-8").read(), "\n\n# Three example suppliers: raw sources and the existing free-text profile\n"]
for sid in ("sup:70110", "sup:70520", "sup:70190"):
    out.append(f"\n## {sid} {b[sid]['name']}\n### Existing free-text profile\n{json.dumps(P[sid], ensure_ascii=False, indent=1)}\n### Raw sources\n{render(sid)}\n")
out.append("""
# Your task
Review the draft schema critically for use by a deterministic negotiation policy. Reply with:
1. Concrete problems (ambiguous fields, enum gaps, fields that cannot be grounded in this kind of source, null vs none semantics, direction ambiguity of levers, missing high-value fields visible in these sources).
2. A revised schema (compact table) with exact definitions and decision rules an extractor can apply consistently.
3. For the 3 examples, the values you would extract under your revised schema, each with source id + verbatim quote.
Be concise. Do not use any tools or files; everything you need is above.""")
open("review_prompt.txt", "w", encoding="utf-8").write("".join(out))
print(len("".join(out)))
