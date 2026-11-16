param()
$ErrorActionPreference = 'Stop'
$python = 'E:\gpt-island\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Required PC Python is unavailable' }
$desktop = [Environment]::GetFolderPath('DesktopDirectory')
$output = Join-Path $desktop '_flicker_shared\mega_t149'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:QT_QPA_PLATFORM = 'windows'
& $python -X utf8 (Join-Path $PSScriptRoot 'run_mega_pc.py') --out $output
exit $LASTEXITCODE
