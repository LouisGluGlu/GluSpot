param(
    [string]$ProjectRoot = (Join-Path $PSScriptRoot '..\..'),
    [string]$PayloadDirectory,
    [string]$CompilerPath,
    [switch]$CheckOnly,
    [switch]$ReuseAssets,
    [switch]$LegacyWindows7
)
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
$taskBranding = Get-Content -LiteralPath (Join-Path $taskRoot 'local\branding.py') -Raw -Encoding UTF8
if ($taskBranding -notmatch 'APP_VERSION = "([0-9.]+)"') { throw 'Cannot read GluSpot version.' }
$taskVersion = $Matches[1]
if (-not $PayloadDirectory) {
    $taskPayloadName = "GluSpot-$taskVersion"
    if ($LegacyWindows7) { $taskPayloadName += '-Win7-x64' }
    $PayloadDirectory = Join-Path $taskRoot ("release\" + $taskPayloadName)
}
$taskPayload = (Resolve-Path -LiteralPath $PayloadDirectory).Path
$taskQtCore = '_internal\PySide6\Qt6Core.dll'
if ($LegacyWindows7) { $taskQtCore = '_internal\PySide2\Qt5Core.dll' }
foreach ($taskItem in @('GluSpot.exe', $taskQtCore, '_internal\online_music\node.exe')) {
    if (-not (Test-Path -LiteralPath (Join-Path $taskPayload $taskItem))) { throw "Incomplete payload: $taskItem" }
}
if (-not $CompilerPath) {
    $taskCandidates = @(
        (Join-Path $taskRoot 'build\installer-toolchain\compiler\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 7\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 7\ISCC.exe')
    )
    $taskCompilerCommand = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($taskCompilerCommand) { $taskCandidates += $taskCompilerCommand.Source }
    $CompilerPath = $taskCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $CompilerPath -or -not (Test-Path -LiteralPath $CompilerPath)) {
    throw 'Install Inno Setup 7 from https://jrsoftware.org/isdl.php and provide -CompilerPath.'
}
$taskCompilerVersion = (& $CompilerPath '--version' 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $taskCompilerVersion -notmatch '^7\.') {
    throw 'The native wizard requires Inno Setup 7. Provide its ISCC.exe with -CompilerPath.'
}
Write-Output "GluSpot $taskVersion payload: $taskPayload"
Write-Output "Inno compiler $taskCompilerVersion`: $CompilerPath"
if ($CheckOnly) { exit 0 }
$taskAssets = Join-Path $taskRoot 'build\installer-player-background'
if ($ReuseAssets) {
    foreach ($taskAsset in @('COMPACT.json', 'compact-brand-299.png', 'compact-lower-3-30.png', 'compact-control-minimize-30.png', 'compact-slogan-30.png')) {
        if (-not (Test-Path -LiteralPath (Join-Path $taskAssets $taskAsset))) {
            throw "Prepared compact installer artwork is missing: $taskAsset. Omit -ReuseAssets to generate it."
        }
    }
    $taskAssetManifest = Get-Content -LiteralPath (Join-Path $taskAssets 'COMPACT.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($taskAssetManifest.raster_density -ne 2) { throw 'Prepared artwork predates the native alpha renderer; omit -ReuseAssets.' }
    if ($taskAssetManifest.progress_presentation -ne 'native-directory-row-capsule-v1') { throw 'Prepared artwork predates the bottom progress bar; omit -ReuseAssets.' }
    if (Get-ChildItem -LiteralPath $taskAssets -Filter 'compact-progress-*.png') { throw 'Retired perimeter progress rasters remain in the cache; omit -ReuseAssets.' }
} else {
    & $taskPython (Join-Path $PSScriptRoot 'prepare-installer-assets.py') $taskAssets --brand-only
    if ($LASTEXITCODE -ne 0) { throw 'Installer artwork preparation failed.' }
}
$taskDefines = @("/DAppVersion=$taskVersion", "/DPayloadDir=$taskPayload", "/DArtifactDir=$(Join-Path $taskRoot 'release')", "/DInstallerAssets=$taskAssets")
if ($LegacyWindows7) { $taskDefines += @('/DPayloadMinVersion=6.1sp1', '/DInstallerSuffix=-Win7', '/DLegacyWindows7=1') }
& $CompilerPath @taskDefines (Join-Path $PSScriptRoot 'gluspot-installer.iss')
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed ($LASTEXITCODE)." }
$taskInstallerName = "GluSpot-$taskVersion"
if ($LegacyWindows7) { $taskInstallerName += '-Win7' }
$taskOutput = Join-Path (Join-Path $taskRoot 'release') ($taskInstallerName + '-Setup-x64.exe')
Get-FileHash -LiteralPath $taskOutput -Algorithm SHA256 | Format-List
Write-Output "Installer ready: $taskOutput"
