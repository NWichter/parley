#!/usr/bin/env bash
# Strength guard before every merge. Run from the root of the build under test, with the flags that ship.
#   eval/guards/check.sh                                   replay only
#   eval/guards/check.sh <results.jsonl> <base> <cand>     replay + persona floors of a paired bank run
# Exit: 0 pass, 2 live check needed (TEXT/LOWER), 1 fail.
set -u
cd "$(dirname "$0")/../.."
uv run --project agent python eval/guards/strengths.py
rc=$?
if [ $# -ge 3 ]; then
  python eval/guards/persona_floors.py "$1" --base "$2" --cand "$3" || rc=1
fi
exit $rc
