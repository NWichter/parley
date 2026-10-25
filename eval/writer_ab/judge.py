"""Blind judging with external CLIs, one call per situation, prompt inline.

    python eval/writer_ab/judge.py codex|agy
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from common import OUT

ROLE = ("You are an experienced reviewer of B2B procurement correspondence (German Mittelstand manufacturing). "
        "Be strict and specific; do not use tools, just read and answer.\n\n")


def codex_cmd():
    """codex.cmd goes through cmd.exe (8 KB command line): call its node entry point directly."""
    exe = Path(shutil.which("codex"))
    js = exe.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    return [shutil.which("node"), str(js)] if js.exists() else [str(exe)]


def call(tool, prompt):
    cwd = tempfile.mkdtemp()
    if tool == "codex":
        last = Path(cwd) / "last.txt"
        subprocess.run([*codex_cmd(), "exec", "--skip-git-repo-check", "-o", str(last), prompt], cwd=cwd,
                       capture_output=True, text=True, encoding="utf-8", timeout=600, stdin=subprocess.DEVNULL)
        return last.read_text(encoding="utf-8") if last.exists() else ""
    r = subprocess.run([shutil.which("agy"), "-p", prompt], cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=600, stdin=subprocess.DEVNULL)
    return r.stdout


def parse(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    return json.loads(m.group(0)) if m else None


def main(tool):
    head = (OUT / "packets" / "HEAD.txt").read_text(encoding="utf-8")
    files = sorted((OUT / "packets").glob("[0-9][0-9].md"))
    dest = OUT / f"judge_{tool}"
    dest.mkdir(exist_ok=True)

    def one(f):
        out = dest / f"{f.stem}.json"
        if out.exists():
            return
        for _attempt in range(2):
            raw = call(tool, ROLE + head + "\n\n" + f.read_text(encoding="utf-8"))
            try:
                j = parse(raw)
            except json.JSONDecodeError:
                j = None
            if j and len(j.get("ratings", {})) == 5:
                out.write_text(json.dumps(j, ensure_ascii=False, indent=1), encoding="utf-8")
                print(tool, f.stem, "ok", flush=True)
                return
            (dest / f"{f.stem}.raw.txt").write_text(raw or "", encoding="utf-8")
        print(tool, f.stem, "FAILED", flush=True)

    with ThreadPoolExecutor(4) as pool:
        list(pool.map(one, files))


if __name__ == "__main__":
    main(sys.argv[1])
