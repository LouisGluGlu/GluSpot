param(
    [Parameter(Mandatory=$true)][string]$PythonPath,
    [switch]$PrepareEnvironment,
    [switch]$Installer,
    [switch]$ReuseAssets
)
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$taskPython = (Resolve-Path -LiteralPath $PythonPath).Path
$taskInfo = & $taskPython -I -c 'import struct,sys; print("%d.%d:%d" % (sys.version_info[0],sys.version_info[1],struct.calcsize("P")*8))'
if ($LASTEXITCODE -ne 0 -or $taskInfo.Trim() -ne '3.8:64') { throw 'Use a separate CPython 3.8 x64 environment for the Win7 build.' }
if ($PrepareEnvironment) {
    & $taskPython -m pip install --disable-pip-version-check -r (Join-Path $PSScriptRoot 'requirements-win7.txt')
    if ($LASTEXITCODE) { throw 'Legacy dependency installation failed.' }
}
$taskLegacyOutput = Join-Path $taskRoot 'release/GluSpot-6.412-Win7-x64'
if (Test-Path -LiteralPath $taskLegacyOutput) { throw 'Preserve the previous legacy package before rebuilding.' }
$taskCheck = & $taskPython -c 'import PySide2,numpy,PIL,zeroconf; print(PySide2.__version__)'
if ($LASTEXITCODE -ne 0 -or $taskCheck.Trim() -ne '5.15.2.1') { throw 'Prepare the pinned requirements-win7.txt environment first.' }
Push-Location -LiteralPath $taskRoot
try {
    & $taskPython -m PyInstaller --clean --noconfirm --distpath release --workpath build/pyinstaller-win7 (Join-Path $PSScriptRoot 'GluSpot-win7.spec')
    if ($LASTEXITCODE) { throw 'Win7 candidate build failed.' }
    if ($Installer) {
        & (Join-Path $PSScriptRoot 'build-installer.ps1') -LegacyWindows7 -ReuseAssets:$ReuseAssets
        if ($LASTEXITCODE) { throw 'Win7 candidate installer build failed.' }
    }
} finally { Pop-Location }
Write-Output ('Experimental Win7 x64 package: ' + $taskLegacyOutput)
Write-Output 'Actual Win7 SP1 acceptance is still required; this build does not create a Win7 VM.'
