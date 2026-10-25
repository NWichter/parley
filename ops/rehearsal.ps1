$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
uv run --script "$PSScriptRoot\rehearsal.py" @args
exit $LASTEXITCODE
