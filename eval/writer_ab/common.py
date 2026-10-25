import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "out"
sys.path.insert(0, str(ROOT / "agent"))


def env(*names):
    """Selected keys from the repo-root .env into os.environ; values are never printed."""
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        k, sep, v = line.partition("=")
        if sep and k.strip() in names:
            os.environ[k.strip()] = v.strip().strip("\"'")
    os.environ.setdefault("ARENA_URL", "https://arena.tacto.ai")
