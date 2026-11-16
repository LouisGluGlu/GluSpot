# Check WebView2, build the Apple Music companion, and start it.
# Prints ASCII only: PASS step=... or FAIL step=... and reason=...
# Windows PowerShell 5.1 reads this file as UTF-8 because it has a BOM.
$ErrorActionPreference = "Stop"

$logDir = if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA "GluSpot" } else { $env:TEMP }
if (-not $logDir) { $logDir = $env:TEMP }
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$record = Join-Path $logDir "apple-music-accept.log"
$companionLog = Join-Path $logDir "apple-music-companion.log"
$lines = New-Object System.Collections.Generic.List[string]

function Write-Record([string]$line) {
    $script:lines.Add($line) | Out-Null
    Write-Output $line
}

function Save-Record {
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllLines($record, $script:lines.ToArray(), $utf8)
}

function Fail([string]$step, [string]$why) {
    Write-Record "FAIL step=$step"
    Write-Record "reason=$why"
    Save-Record
    exit 1
}

function Pass([string]$step, [string]$detail) {
    Write-Record "PASS step=$step detail=$detail"
}

function Read-SharedText([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return "" }
    $stream = $null
    try {
        $stream = [System.IO.File]::Open($path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8)
        return $reader.ReadToEnd()
    } catch {
        return ""
    } finally {
        if ($stream) { $stream.Dispose() }
    }
}

function Clip([string]$text) {
    $flat = ([string]$text) -replace '\s+', ' '
    if ($flat.Length -gt 400) { return $flat.Substring(0, 400) }
    return $flat.Trim()
}

if ($env:OS -ne "Windows_NT") {
    Fail "os" "This script runs on Windows only."
}

$version = [Environment]::OSVersion.Version
if ($version.Major -lt 10 -or ($version.Major -eq 10 -and $version.Build -lt 22000)) {
    Write-Record "NOTE step=os detail=Not Windows 11. The companion is built for Windows 11. Checks continue."
}

$guid = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
$keys = @(
    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\$guid",
    "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$guid",
    "HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$guid"
)
$webViewVersion = ""
foreach ($key in $keys) {
    if (Test-Path -LiteralPath $key) {
        $item = Get-ItemProperty -LiteralPath $key -ErrorAction SilentlyContinue
        if ($item -and $item.pv) {
            $webViewVersion = [string]$item.pv
            break
        }
    }
}
if (-not $webViewVersion) {
    Fail "webview2" "WebView2 Evergreen runtime was not found. Install Microsoft Edge WebView2 Runtime."
}
Pass "webview2" "version $webViewVersion"

$ndp = Get-ItemProperty -LiteralPath "HKLM:\SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full" -ErrorAction SilentlyContinue
if (-not $ndp -or -not $ndp.Release -or [int]$ndp.Release -lt 528040) {
    Fail "dotnet-framework" ".NET Framework 4.8 was not found. Release must be 528040 or newer."
}
Pass "dotnet-framework" "Release $($ndp.Release)"

$dotnet = Get-Command dotnet -ErrorAction SilentlyContinue
if (-not $dotnet) {
    Fail "dotnet" "dotnet was not found. Install the .NET SDK, then run this script again."
}
Pass "dotnet" $dotnet.Source

$project = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$csproj = Join-Path $PSScriptRoot "GluSpotAppleMusicCompanion.csproj"
Push-Location $project
try {
    & $dotnet.Source build $csproj -c Release
    if ($LASTEXITCODE -ne 0) {
        Fail "build" "dotnet build exited with code $LASTEXITCODE. The compiler text is above."
    }
} catch {
    Fail "build" (Clip $_.Exception.Message)
} finally {
    Pop-Location
}
Pass "build" $csproj

$exe = Join-Path $PSScriptRoot "bin\Release\net48\GluSpotAppleMusicCompanion.exe"
if (-not (Test-Path -LiteralPath $exe)) {
    Fail "start" "Build finished but the executable was not found at $exe"
}

$stamp = [Guid]::NewGuid().ToString("N")
$outFile = Join-Path $env:TEMP "gluspot-apple-accept-$stamp-out.txt"
$errFile = Join-Path $env:TEMP "gluspot-apple-accept-$stamp-err.txt"
$inFile = Join-Path $env:TEMP "gluspot-apple-accept-$stamp-in.txt"
Set-Content -LiteralPath $inFile -Value "" -Encoding ascii
$proc = $null
try {
    # Normal style so the first window stays on a monitor. A hidden or
    # off-screen window makes WebView2 initialization fail.
    $proc = Start-Process -FilePath $exe -PassThru `
        -RedirectStandardOutput $outFile -RedirectStandardError $errFile -RedirectStandardInput $inFile
} catch {
    Fail "start" (Clip $_.Exception.Message)
}
if (-not $proc) {
    Fail "start" "The process did not start."
}

$deadline = (Get-Date).AddSeconds(20)
$stdout = ""
while ((Get-Date) -lt $deadline) {
    $stdout = Read-SharedText $outFile
    if ($stdout -match '"ok"\s*:\s*false') { break }
    if ($proc.HasExited) { break }
    Start-Sleep -Milliseconds 400
}
$stdout = Read-SharedText $outFile

$code = ""
$exceptionName = ""
$message = ""
if ($stdout -match '"error"\s*:\s*"([^"]*)"') { $code = $Matches[1] }
if ($stdout -match '"exception"\s*:\s*"([^"]*)"') { $exceptionName = $Matches[1] }
if ($stdout -match '"message"\s*:\s*"([^"]*)"') { $message = $Matches[1] }
if ($code -or $exceptionName -or $message) {
    if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
    $why = "error=$code exception=$exceptionName message=$(Clip $message)"
    $last = ""
    $logged = Read-SharedText $companionLog
    foreach ($row in ($logged -split "`n")) {
        $trim = $row.Trim()
        if ($trim) { $last = $trim }
    }
    if ($last) { $why = "$why log=$(Clip $last)" }
    Fail "start" $why
}
if ($proc.HasExited) {
    $errText = Clip (Read-SharedText $errFile)
    $extra = ""
    if ($errText) { $extra = " stderr=$errText" }
    Fail "start" "process exited code=$($proc.ExitCode).$extra See $companionLog"
}

$logged = Read-SharedText $companionLog
if ($logged -notmatch 'started') {
    if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
    Fail "start" "companion log has no started line. The process must create the GluSpot directory and write started before WebView2 work. See $companionLog"
}
Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
Pass "start" "The process stayed up and the companion log contains started. This script then closed it. Log: $companionLog"
Write-Record "NOTE step=login detail=The login window stays open until the user finishes or closes it. That wait does not use the short request timeout and does not stop the companion. Closing the window or opening GluSpot sends an account request and refreshes the source light."
Write-Record "record=$record"
Save-Record
exit 0
