$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
uv run --script "$PSScriptRoot\final.py" @args
exit $LASTEXITCODE
