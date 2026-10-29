param()
$ErrorActionPreference = 'Stop'
$python = 'E:\gpt-island\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Required PC Python is unavailable' }
$desktop = [Environment]::GetFolderPath('DesktopDirectory')
$output = Join-Path $desktop '_flicker_shared\mega_t156'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:QT_QPA_PLATFORM = 'windows'
foreach ($scale in @(1, 2)) {
    $env:QT_SCALE_FACTOR = [string]$scale
    $output = Join-Path $desktop ("_flicker_shared\mega_t156\dpr" + $scale)
    & $python -X utf8 (Join-Path $PSScriptRoot 'run_mega_pc.py') --out $output
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
exit 0
