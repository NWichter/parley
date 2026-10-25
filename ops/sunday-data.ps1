$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
uv run --script "$PSScriptRoot\sunday_data.py" @args
exit $LASTEXITCODE
