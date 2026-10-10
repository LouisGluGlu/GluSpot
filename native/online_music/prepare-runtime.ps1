param(
    [string]$NodePath = "",
    [string]$NpmPath = "",
    [string]$NodeLicensePath = ""
)
$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$runtimeRoot = Join-Path $projectRoot "build/online-music"
if (-not $NodePath) {
    $bundledNode = Join-Path $env:USERPROFILE ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
    if (Test-Path -LiteralPath $bundledNode) { $NodePath = $bundledNode }
    else { $NodePath = (Get-Command node -ErrorAction Stop).Source }
}
if (-not $NpmPath) { $NpmPath = (Get-Command npm.cmd -ErrorAction Stop).Source }
$NodePath = (Resolve-Path -LiteralPath $NodePath).Path
$NpmPath = (Resolve-Path -LiteralPath $NpmPath).Path
$nodeVersion = (& $NodePath --version).Trim()
if ($nodeVersion -notmatch '^v(?:2[0246]|[3-9][0-9])\.') {
    throw "A maintained Node.js version 20 or later is required"
}
New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "package.json") -Destination $runtimeRoot -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "package-lock.json") -Destination $runtimeRoot -Force
Push-Location $runtimeRoot
try {
    & $NpmPath ci --ignore-scripts --omit=dev --no-audit --no-fund --registry=https://registry.npmjs.org
    if ($LASTEXITCODE -ne 0) { throw "Online music adapter installation failed" }
} finally { Pop-Location }
Copy-Item -LiteralPath $NodePath -Destination (Join-Path $runtimeRoot "node.exe") -Force
$noticesRoot = Join-Path $runtimeRoot "licenses"
New-Item -ItemType Directory -Path $noticesRoot -Force | Out-Null
$nodeLicense = Join-Path $noticesRoot "Node-LICENSE.txt"
if ($NodeLicensePath) {
    Copy-Item -LiteralPath (Resolve-Path -LiteralPath $NodeLicensePath).Path -Destination $nodeLicense -Force
} else {
    # Node's release-specific LICENSE also contains its bundled dependency notices.
    $licenseUrl = "https://raw.githubusercontent.com/nodejs/node/$nodeVersion/LICENSE"
    Invoke-WebRequest -Uri $licenseUrl -OutFile $nodeLicense
}
# Record hashes and retain package licenses for reproducible optional packaging.
& (Join-Path $runtimeRoot "node.exe") (Join-Path $PSScriptRoot "verify-runtime.cjs") $runtimeRoot
if ($LASTEXITCODE -ne 0) { throw "Online music runtime verification failed" }
Write-Output "GluSpot optional online music runtime prepared at $runtimeRoot"
