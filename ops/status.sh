#!/usr/bin/env bash
set -euo pipefail
export PYTHONUTF8=1
exec uv run --script "$(cd "$(dirname "$0")" && pwd)/status.py" "$@"
