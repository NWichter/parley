import json, subprocess, sys, time, shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import lib
tool = sys.argv[1]
ids = sys.argv[3:] or sorted(lib.B)
workers = int(sys.argv[2])
OUT = lib.HERE / "out" / tool
def cmd(prompt):
    if tool == "gemini":
        return [shutil.which("agy"), "--model", "gemini-4-argon-high", "-p", prompt]
    return [shutil.which("codex"), "exec", "--skip-git-repo-check", "-"]
def one(sid):
    f = OUT / (sid.replace(":", "_") + ".json")
    if f.exists():
        return sid, "cached"
    prompt = lib.full_prompt(sid) + "\n\nDo not use any tools or files; everything you need is above. Reply with the JSON object only."
    for attempt in range(3):
        t0 = time.time()
        try:
            r = subprocess.run(cmd(prompt), input=prompt if tool == "codex" else None, capture_output=True, text=True, encoding="utf-8", timeout=900, cwd=str(lib.HERE))
            text = r.stdout
            data = lib.parse_json(text)
            f.write_text(json.dumps({"raw": text, "data": data, "s": round(time.time() - t0, 1)}, ensure_ascii=False, indent=1), encoding="utf-8")
            return sid, f"ok {time.time()-t0:.0f}s"
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:200]}"
    return sid, "FAIL " + err
with ThreadPoolExecutor(workers) as p:
    for sid, s in p.map(one, ids):
        print(sid, s, flush=True)
