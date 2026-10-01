param(
    [string]$ToolchainBin = "",
    [string]$BuildDirectory = "build/native-airplay",
    [switch]$SkipTests
)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$nativeBuildPath = [IO.Path]::GetFullPath((Join-Path $projectRoot $BuildDirectory))
$cmakePath = Join-Path $projectRoot '.venv/Scripts/cmake.exe'
if (-not (Test-Path -LiteralPath $cmakePath)) {
    $cmakePath = (Get-Command cmake -ErrorAction Stop).Source
}
$originalPath = $env:PATH
if (-not $ToolchainBin) {
    $workspaceToolchain = Join-Path $projectRoot 'build/airplay-tools/llvm-mingw-20260922-ucrt-x86_64/bin'
    if (Test-Path -LiteralPath $workspaceToolchain) { $ToolchainBin = $workspaceToolchain }
}
try {
    $env:PATH = (Join-Path $projectRoot '.venv/Scripts') + ';' + $env:PATH
    $configureArgs = @('-S', (Join-Path $projectRoot 'native/airplay_sender'),
                       '-B', $nativeBuildPath, '-DCMAKE_BUILD_TYPE=Release')
    if ($ToolchainBin) {
        $nativeToolchain = (Resolve-Path -LiteralPath $ToolchainBin).Path
        $env:PATH = $nativeToolchain + ';' + $env:PATH
        $configureArgs += @('-G', 'Ninja',
            ('-DCMAKE_C_COMPILER=' + (Join-Path $nativeToolchain 'x86_64-w64-mingw32-clang.exe')),
            ('-DCMAKE_CXX_COMPILER=' + (Join-Path $nativeToolchain 'x86_64-w64-mingw32-clang++.exe')))
    }
    $configureArgs += ('-DGLUSPOT_AIRPLAY_TESTS=' + $(if ($SkipTests) { 'OFF' } else { 'ON' }))
    & $cmakePath @configureArgs
    if ($LASTEXITCODE) { throw 'AirPlay CMake configure failed' }
    & $cmakePath --build $nativeBuildPath --config Release --parallel
    if ($LASTEXITCODE) { throw 'AirPlay native compile failed' }
    if (-not $SkipTests) {
        $ctestPath = Join-Path (Split-Path -Parent $cmakePath) 'ctest.exe'
        & $ctestPath --test-dir $nativeBuildPath -C Release --output-on-failure
        if ($LASTEXITCODE) { throw 'AirPlay native tests failed' }
    }
    $licensePath = Join-Path $nativeBuildPath 'licenses'
    New-Item -ItemType Directory -Path $licensePath -Force | Out-Null
    $vendorPath = Join-Path $projectRoot 'third_party/airplay2-sender-cpp'
    foreach ($entry in @(@('LICENSE', 'airplay2-sender-APACHE-2.0.txt'),
                          @('NOTICE', 'airplay2-sender-NOTICE.txt'),
                          @('licenses/THIRD-PARTY-NOTICES.txt', 'THIRD-PARTY-NOTICES.txt'),
                          @('third_party/ed25519/LICENSE.txt', 'ed25519-zlib.txt'),
                          @('GLUSPOT-PROVENANCE.md', 'GLUSPOT-PROVENANCE.md'))) {
        Copy-Item -LiteralPath (Join-Path $vendorPath $entry[0]) -Destination (Join-Path $licensePath $entry[1]) -Force
    }
    $mbedSource = Join-Path $nativeBuildPath '_deps/mbedtls-src'
    foreach ($candidate in @('LICENSE', 'LICENSES/Apache-2.0.txt', 'LICENSES/GPL-2.0-or-later.txt')) {
        $dependencyLicense = Join-Path $mbedSource $candidate
        if (Test-Path -LiteralPath $dependencyLicense) {
            Copy-Item -LiteralPath $dependencyLicense -Destination (Join-Path $licensePath ('mbedtls-' + [IO.Path]::GetFileName($candidate))) -Force
        }
    }
    if ($ToolchainBin) {
        $toolchainLicense = Join-Path (Split-Path -Parent $nativeToolchain) 'LICENSE.TXT'
        if (Test-Path -LiteralPath $toolchainLicense) {
            Copy-Item -LiteralPath $toolchainLicense -Destination (Join-Path $licensePath 'llvm-mingw-RUNTIME-LICENSES.txt') -Force
        }
        $mingwNotices = Join-Path (Split-Path -Parent $nativeToolchain) 'x86_64-w64-mingw32/share/mingw32'
        foreach ($notice in @('COPYING', 'COPYING.MinGW-w64.txt', 'COPYING.MinGW-w64-runtime.txt', 'COPYING.winpthreads.txt')) {
            $runtimeNotice = Join-Path $mingwNotices $notice
            if (Test-Path -LiteralPath $runtimeNotice) {
                Copy-Item -LiteralPath $runtimeNotice -Destination (Join-Path $licensePath ('mingw-' + $notice)) -Force
            }
        }
    }
    Write-Output ('Built ' + (Join-Path $nativeBuildPath 'GluSpotAirPlay.exe'))
} finally {
    $env:PATH = $originalPath
}
