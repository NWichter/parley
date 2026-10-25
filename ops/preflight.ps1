$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
uv run --script "$PSScriptRoot\preflight.py" @args
exit $LASTEXITCODE
