$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
uv run --script "$PSScriptRoot\status.py" @args
exit $LASTEXITCODE
