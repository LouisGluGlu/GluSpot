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
$nodeNotice = Join-Path $noticesRoot "Node-LICENSE-unavailable.txt"
$verifyArguments = @($runtimeRoot)
if ($NodeLicensePath) {
    Copy-Item -LiteralPath (Resolve-Path -LiteralPath $NodeLicensePath).Path -Destination $nodeLicense -Force
} else {
    $localLicense = @("LICENSE", "LICENSE.txt", "Node-LICENSE.txt") |
        ForEach-Object { Join-Path (Split-Path -Parent $NodePath) $_ } |
        Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
        Select-Object -First 1
    if ($localLicense) {
        Copy-Item -LiteralPath $localLicense -Destination $nodeLicense -Force
    } else {
        # Prefer the matching local distribution's full third-party notices.
        # A failed notice download must not undo a working npm ci/Node copy:
        # retain an explicit development-only notice and still verify adapters.
        # Packaging continues to require the full Node-LICENSE.txt.
        $licenseUrl = "https://raw.githubusercontent.com/nodejs/node/$nodeVersion/LICENSE"
        $licenseDownload = "$nodeLicense.download"
        try {
            Invoke-WebRequest -Uri $licenseUrl -OutFile $licenseDownload -TimeoutSec 20
            Move-Item -LiteralPath $licenseDownload -Destination $nodeLicense -Force
        } catch {
            Remove-Item -LiteralPath $nodeLicense -Force -ErrorAction SilentlyContinue
            @"
GluSpot optional Node.js runtime: full release LICENSE unavailable.
Node.js $nodeVersion; source: $licenseUrl
The license download failed. This notice is not the Node.js license.
For distribution, retain the complete release LICENSE using -NodeLicensePath.
"@ | Set-Content -LiteralPath $nodeNotice -Encoding UTF8
            $verifyArguments += "--allow-node-license-notice"
            Write-Warning "Node LICENSE download unavailable; retained a notice. Full release LICENSE is required before packaging."
        } finally {
            Remove-Item -LiteralPath $licenseDownload -Force -ErrorAction SilentlyContinue
        }
    }
}
if (Test-Path -LiteralPath $nodeLicense) {
    Remove-Item -LiteralPath $nodeNotice -Force -ErrorAction SilentlyContinue
}
# Record hashes and retain package licenses for reproducible optional packaging.
& (Join-Path $runtimeRoot "node.exe") (Join-Path $PSScriptRoot "verify-runtime.cjs") @verifyArguments
if ($LASTEXITCODE -ne 0) { throw "Online music runtime verification failed" }
Write-Output "GluSpot optional online music runtime prepared at $runtimeRoot"
