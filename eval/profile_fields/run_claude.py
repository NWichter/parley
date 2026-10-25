import json, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import anthropic, lib
client = anthropic.Anthropic(api_key=lib.env_key("ANTHROPIC_API_KEY_2"), max_retries=3, timeout=300)
OUT = lib.HERE / "out" / "claude"
ids = sys.argv[1:] or sorted(lib.B)
def one(sid):
    f = OUT / (sid.replace(":", "_") + ".json")
    if f.exists():
        return sid, "cached"
    t0 = time.time()
    r = client.messages.create(model="claude-opus-5-5", max_tokens=16000, system=lib.PROMPT,
                               messages=[{"role": "user", "content": lib.user_msg(sid)}], output_config={"effort": "high"})
    text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
    try:
        data = lib.parse_json(text)
    except Exception as e:
        data = {"_error": str(e)}
    f.write_text(json.dumps({"raw": text, "data": data, "usage": {"in": r.usage.input_tokens, "out": r.usage.output_tokens}, "s": round(time.time() - t0, 1)}, ensure_ascii=False, indent=1), encoding="utf-8")
    return sid, f"{r.usage.input_tokens}/{r.usage.output_tokens} {'ERR' if '_error' in data else 'ok'}"
with ThreadPoolExecutor(8) as p:
    for sid, s in p.map(one, ids):
        print(sid, s, flush=True)
