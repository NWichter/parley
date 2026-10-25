"""Our best live threads must stay reachable: replayed with the flags they were played with, no round
may ask for terms that cost us more (eval/guards/strengths.py; the merge gate runs it strictly)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from parley.dossier import load_package

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / "eval" / "guards" / "strengths.py"
THREADS = sorted((ROOT / "eval" / "guards" / "threads").glob("*.json"))


@pytest.mark.parametrize("path", THREADS, ids=[p.stem.split("-")[0] for p in THREADS])
def test_best_thread_not_worse(path, tmp_path):
    if load_package() is None:
        pytest.skip("no data package")
    env = json.loads(path.read_text(encoding="utf-8")).get("record_env") or {}
    out = tmp_path / "guard.json"
    args = [sys.executable, str(GUARD), "--threads", str(path), "--json", str(out)]
    for k, v in env.items():
        args += ["--env", f"{k}={v}"]
    subprocess.run(args, capture_output=True, text=True, timeout=120)
    res = json.loads(out.read_text(encoding="utf-8"))[0]
    assert res["verdict"] not in ("WORSE", "ERROR"), res["detail"]
