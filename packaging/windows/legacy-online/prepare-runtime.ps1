param(
    [string]$SourceRuntime = 'build/online-music',
    [string]$OutputDirectory = 'build/online-music-win7',
    [string]$NodeArchive = '',
    [string]$BuildNode = '',
    [string]$NpmPath = ''
)
$ErrorActionPreference = 'Stop'
$taskProject = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
$taskSource = [IO.Path]::GetFullPath((Join-Path $taskProject $SourceRuntime))
$taskOutput = [IO.Path]::GetFullPath((Join-Path $taskProject $OutputDirectory))
if ($taskOutput -eq $taskProject -or -not $taskOutput.StartsWith($taskProject + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Legacy output must be a dedicated directory inside the project.'
}
if ((Test-Path -LiteralPath $taskOutput) -and (Get-ChildItem -LiteralPath $taskOutput -Force | Select-Object -First 1)) {
    throw 'Preserve the previous legacy output before rebuilding; the output directory must be empty.'
}
if (-not $BuildNode) { $BuildNode = Join-Path $taskSource 'node.exe' }
if (-not $NpmPath) { $NpmPath = (Get-Command npm.cmd -ErrorAction Stop).Source }
$taskTools = Join-Path $taskProject 'build/legacy-online-tooling'
$taskNodeCache = Join-Path $taskProject 'build/windows-legacy-node/13.14.0'
New-Item -ItemType Directory -Path $taskTools, $taskNodeCache -Force | Out-Null
$taskArchiveName = 'node-v13.14.0-win-x64.zip'
$taskExpectedHash = '8c6e77fac5e911a2e70b6ca34804b3b59b6a3c685ab4e3b17756397df86063fa'
$taskArchive = Join-Path $taskNodeCache $taskArchiveName
if ($NodeArchive) { $taskArchive = (Resolve-Path -LiteralPath $NodeArchive).Path }
elseif (-not (Test-Path -LiteralPath $taskArchive)) {
    Invoke-WebRequest -UseBasicParsing -Uri ('https://nodejs.org/dist/v13.14.0/' + $taskArchiveName) -OutFile $taskArchive
}
if ((Get-FileHash -LiteralPath $taskArchive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskExpectedHash) {
    throw 'Official Node 13.14.0 archive SHA-256 mismatch.'
}
Expand-Archive -LiteralPath $taskArchive -DestinationPath $taskNodeCache -Force
$taskLegacyNode = Join-Path $taskNodeCache 'node-v13.14.0-win-x64/node.exe'
if ((& $taskLegacyNode --version).Trim() -ne 'v13.14.0') { throw 'Unexpected legacy Node version.' }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'package.json'), (Join-Path $PSScriptRoot 'package-lock.json') -Destination $taskTools -Force
& $NpmPath ci --prefix $taskTools --ignore-scripts --no-audit --no-fund --registry=https://registry.npmjs.org
if ($LASTEXITCODE) { throw 'Pinned legacy build-tool/runtime-shim preparation failed.' }
& $BuildNode (Join-Path $PSScriptRoot 'build-runtime.cjs') --source $taskSource --output $taskOutput --tooling $taskTools --node $taskLegacyNode
if ($LASTEXITCODE) { throw 'Separate legacy online-runtime transformation failed.' }
& $taskLegacyNode (Join-Path $PSScriptRoot 'verify-runtime.cjs') $taskOutput (Join-Path $taskOutput 'OFFLINE-ACCEPTANCE.json')
if ($LASTEXITCODE) { throw 'Legacy online-runtime offline acceptance failed.' }
Write-Output ('Experimental Win7 online runtime: ' + $taskOutput)
