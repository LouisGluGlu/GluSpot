[CmdletBinding(PositionalBinding = $false)]
param(
    [string[]]$ProjectRoot = @(),
    [string]$PythonPath = '',
    [switch]$CheckOnly,
    [switch]$NoPause,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Folders = @()
)

# Python environment only. This script does not launch GluSpot, change global
# PATH, install Python, touch accounts/settings or prepare the optional Node API.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
if (-not $env:PIP_DEFAULT_TIMEOUT) { $env:PIP_DEFAULT_TIMEOUT = '60' }
if (-not $env:PIP_RETRIES) { $env:PIP_RETRIES = '5' }

$probeCode = @'
import json, os, platform, struct, sys
def readonly(event, args):
    if event == 'open':
        mode, flags = args[1], args[2]
        if (isinstance(mode, str) and any(c in mode for c in 'wax+')) or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            raise PermissionError('Interpreter probes forbid filesystem writes')
    if event in ('os.mkdir', 'os.remove', 'os.rename', 'os.rmdir', 'os.link', 'os.symlink', 'os.truncate', 'os.chmod', 'os.utime'):
        raise PermissionError('Interpreter probes forbid filesystem writes')
sys.addaudithook(readonly)
# -S delays .pth/sitecustomize until the readonly guard is installed.
import site
site.main()
print(json.dumps(dict(version=list(sys.version_info[:3]), implementation=platform.python_implementation(), bits=struct.calcsize('P')*8, platform=sys.platform, executable=os.path.realpath(sys.executable), prefix=os.path.realpath(sys.prefix), base_prefix=os.path.realpath(sys.base_prefix), base_executable=os.path.realpath(sys._base_executable), search_paths=[os.path.realpath(p) for p in sys.path if p])))
'@

$doctorCode = @'
import importlib, importlib.metadata, json, os, pathlib, sys
failures=[]
if sys.argv[2] == 'readonly':
    # -B prevents bytecode; the audit hook also prevents import-time generators
    # (such as COM wrapper caches) from writing during a check-only invocation.
    def readonly(event, args):
        if event == 'open':
            mode, flags = args[1], args[2]
            if (isinstance(mode, str) and any(c in mode for c in 'wax+')) or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                raise PermissionError('CheckOnly forbids filesystem writes')
        if event in ('os.mkdir', 'os.remove', 'os.rename', 'os.rmdir', 'os.link', 'os.symlink', 'os.truncate', 'os.chmod', 'os.utime'):
            raise PermissionError('CheckOnly forbids filesystem writes')
    sys.addaudithook(readonly)
import site
site.main()
try:
    from packaging.requirements import Requirement
    for line in pathlib.Path(sys.argv[1]).read_text(encoding='utf-8-sig').splitlines():
        line=line.strip()
        if not line or line.startswith('#'):
            continue
        requirement=Requirement(line)
        if requirement.marker and not requirement.marker.evaluate():
            continue
        try:
            version=importlib.metadata.version(requirement.name)
            if version not in requirement.specifier:
                failures.append(requirement.name+': version '+version+' does not satisfy '+str(requirement.specifier))
        except importlib.metadata.PackageNotFoundError:
            failures.append(requirement.name+': not installed')
except Exception as error:
    failures.append('requirement check: '+type(error).__name__)
for name in ('pip', 'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets', 'PySide6.QtMultimedia', 'winsdk.windows.media.control', 'soundcard', 'numpy', 'pywinauto', 'uiautomation', 'PIL', 'PyInstaller', 'ToJyutping', 'zeroconf'):
    try:
        module=importlib.import_module(name)
        origin=getattr(module, '__file__', None)
        if origin and not pathlib.Path(origin).resolve().is_relative_to(pathlib.Path(sys.prefix).resolve()):
            failures.append(name+': imported outside the local environment')
    except Exception as error:
        failures.append(name+': '+type(error).__name__)
print(json.dumps(dict(ready=not failures, failures=failures), ensure_ascii=True))
sys.exit(0 if not failures else 7)
'@

function Quote-NativeArgument([string]$Value) {
    # Windows CommandLineToArgvW quoting; no shell/string-built command runs.
    $quoted = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $quoted = [regex]::Replace($quoted, '(\\+)$', '$1$1')
    return '"' + $quoted + '"'
}

function Invoke-Captured([string]$Executable, [string[]]$Arguments, [int]$TimeoutSeconds = 30) {
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = $Executable
    $start.Arguments = (($Arguments | ForEach-Object { Quote-NativeArgument $_ }) -join ' ')
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $start.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $start
    try {
        [void]$process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $process.Kill()
            $process.WaitForExit()
            return @{ Code = 7; Output = ''; Error = 'Interpreter check timed out.' }
        }
        return @{ Code = $process.ExitCode; Output = $stdout.Result; Error = $stderr.Result }
    }
    catch { return @{ Code = 7; Output = ''; Error = 'Interpreter could not be executed.' } }
    finally { $process.Dispose() }
}

function Get-PythonInfo([string]$Executable, [string[]]$PrefixArguments = @()) {
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) { return $null }
    $result = Invoke-Captured $Executable ($PrefixArguments + @('-I', '-S', '-B', '-c', $probeCode)) 15
    if ($result.Code -ne 0) { return $null }
    try { return ($result.Output.Trim() | ConvertFrom-Json) }
    catch { return $null }
}

function Test-CompatiblePython($Info) {
    return $null -ne $Info -and $Info.implementation -eq 'CPython' -and
        $Info.version[0] -eq 3 -and $Info.version[1] -eq 12 -and
        $Info.bits -eq 64 -and $Info.platform -eq 'win32'
}

function Test-SamePath([string]$Left, [string]$Right) {
    return [string]::Equals([IO.Path]::GetFullPath($Left).TrimEnd('\'),
        [IO.Path]::GetFullPath($Right).TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)
}

function Assert-EnvironmentPath([string]$Root, [string]$Venv) {
    if (-not (Test-SamePath $Venv (Join-Path $Root '.venv')) -or
        -not (Test-SamePath ([IO.Path]::GetDirectoryName($Venv)) $Root)) {
        throw 'Environment target must be exactly the selected project .venv.'
    }
    $current = $Venv
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Redirected environment path refused: $current"
            }
        }
        $current = [IO.Path]::GetDirectoryName($current)
    }
    foreach ($relative in @('Scripts', 'Scripts/python.exe', 'Lib', 'Lib/site-packages', 'pyvenv.cfg')) {
        $path = Join-Path $Venv $relative
        if (Test-Path -LiteralPath $path) {
            if ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Redirected environment path refused: $path"
            }
        }
    }
    if ((Test-Path -LiteralPath $Venv) -and -not (Test-Path -LiteralPath $Venv -PathType Container)) {
        throw 'The project .venv exists but is not a directory.'
    }
}

function Get-LocalEnvironment([string]$Venv) {
    $python = Join-Path $Venv 'Scripts/python.exe'
    $config = Join-Path $Venv 'pyvenv.cfg'
    if (-not (Test-Path -LiteralPath $config -PathType Leaf)) { return $null }
    $configuration = Get-Content -LiteralPath $config -Raw -Encoding UTF8
    if ($configuration -notmatch '(?im)^include-system-site-packages\s*=\s*false\s*$') { return $null }
    # Windows can execute a copied venv while pip.exe and other console
    # launchers still point to the original folder. CPython 3.12 records the
    # absolute creation target as the final command field (without quoting).
    $command = [regex]::Match($configuration, '(?im)^command\s*=\s*(.+)$')
    if (-not $command.Success) { return $null }
    $creation = $command.Groups[1].Value.Trim().Replace('/', '\')
    if (-not ($creation.EndsWith(' ' + $Venv, [StringComparison]::OrdinalIgnoreCase) -or
        $creation.EndsWith(' "' + $Venv + '"', [StringComparison]::OrdinalIgnoreCase))) { return $null }
    $info = Get-PythonInfo $python
    if (-not (Test-CompatiblePython $info)) { return $null }
    if (-not (Test-SamePath $info.prefix $Venv) -or (Test-SamePath $info.prefix $info.base_prefix)) { return $null }
    $base = Get-PythonInfo $info.base_executable
    if (-not (Test-CompatiblePython $base) -or -not (Test-SamePath $base.prefix $base.base_prefix)) { return $null }
    foreach ($searchPath in $info.search_paths) {
        $withinLocal = (Test-SamePath $searchPath $Venv) -or $searchPath.StartsWith($Venv + '\', [StringComparison]::OrdinalIgnoreCase)
        $withinBase = (Test-SamePath $searchPath $base.prefix) -or $searchPath.StartsWith($base.prefix + '\', [StringComparison]::OrdinalIgnoreCase)
        if (-not $withinLocal -and (-not $withinBase -or $searchPath -match '(?i)[\\/]site-packages([\\/]|$)')) { return $null }
    }
    $executable = [regex]::Match($configuration, '(?im)^executable\s*=\s*(.+)$')
    if (-not $executable.Success -or -not (Test-SamePath $executable.Groups[1].Value.Trim() $base.executable)) { return $null }
    return @{ Python = $python; Info = $info; Base = $base.executable }
}

function Find-BasePython([string]$Selected) {
    if ($Selected) {
        $candidate = $Selected.Trim().Trim('"')
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $candidate = (Resolve-Path -LiteralPath $candidate).ProviderPath
        }
        $info = Get-PythonInfo $candidate
        if (-not (Test-CompatiblePython $info)) { return $null }
        $base = Get-PythonInfo $info.base_executable
        if (Test-CompatiblePython $base) { return $base.executable }
        return $null
    }
    # py is a candidate, not proof; PATH can contain an incompatible MSYS Python.
    $launcher = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($launcher) {
        $info = Get-PythonInfo $launcher.Source @('-3.12')
        if (Test-CompatiblePython $info) {
            $base = Get-PythonInfo $info.base_executable
            if (Test-CompatiblePython $base) { return $base.executable }
        }
    }
    $candidates = @()
    if ($env:LOCALAPPDATA) { $candidates += Join-Path $env:LOCALAPPDATA 'Programs/Python/Python312/python.exe' }
    if ($env:ProgramFiles) { $candidates += Join-Path $env:ProgramFiles 'Python312/python.exe' }
    $candidates += 'C:/Python312/python.exe'
    foreach ($name in @('python.exe', 'python3.exe')) {
        $candidates += @(Get-Command $name -CommandType Application -All -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
    }
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        $info = Get-PythonInfo $candidate
        if (Test-CompatiblePython $info) {
            $base = Get-PythonInfo $info.base_executable
            if (Test-CompatiblePython $base) { return $base.executable }
        }
    }
    return $null
}

function Test-Dependencies([string]$Python, [string]$Requirements) {
    $mode = 'repair'
    if ($CheckOnly) { $mode = 'readonly' }
    $result = Invoke-Captured $Python @('-I', '-S', '-B', '-c', $doctorCode, $Requirements, $mode) 45
    try {
        $report = $result.Output.Trim() | ConvertFrom-Json
        if ($result.Code -eq 0 -and $report.ready) { return $true }
        foreach ($failure in $report.failures) { Write-Host "  $failure" }
    }
    catch { Write-Host '  Dependency check did not complete.' }
    return $false
}

function Repair-Project([string]$RequestedRoot) {
    try {
        $resolved = Resolve-Path -LiteralPath $RequestedRoot
        if ($resolved.Provider.Name -ne 'FileSystem') { throw 'A filesystem project folder is required.' }
        $root = [IO.Path]::GetFullPath($resolved.ProviderPath).TrimEnd('\')
        if (-not (Test-Path -LiteralPath $root -PathType Container) -or
            -not (Test-Path -LiteralPath (Join-Path $root 'local/main.py') -PathType Leaf) -or
            -not (Test-Path -LiteralPath (Join-Path $root 'requirements.txt') -PathType Leaf)) {
            throw 'Project folder must contain local/main.py and requirements.txt.'
        }
    }
    catch { Write-Host "Invalid project folder: $RequestedRoot"; return 2 }
    Write-Host "GluSpot Python environment: $root"
    $venv = [IO.Path]::GetFullPath((Join-Path $root '.venv'))
    try { Assert-EnvironmentPath $root $venv }
    catch { Write-Host $_.Exception.Message; return 4 }
    $local = Get-LocalEnvironment $venv
    $requirements = Join-Path $root 'requirements.txt'
    if ($CheckOnly) {
        if (-not $local) { Write-Host 'Local CPython 3.12 x64 environment is missing, relocated or invalid.'; return 7 }
        if (-not (Test-Dependencies $local.Python $requirements)) { return 7 }
        Write-Host 'Environment is ready.'
        return 0
    }
    if (-not $local) {
        $selected = $PythonPath
        if (-not $selected) { $selected = $env:GLUSPOT_PYTHON }
        $base = Find-BasePython $selected
        if (-not $base) {
            Write-Host 'No compatible CPython 3.12 x64 was found. Use -PythonPath or GLUSPOT_PYTHON with its python.exe.'
            return 3
        }
        try {
            Assert-EnvironmentPath $root $venv
            if (Test-Path -LiteralPath $venv) {
                $backup = Join-Path $root ('.venv.backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
                if (-not (Test-SamePath ([IO.Path]::GetDirectoryName($backup)) $root)) { throw 'Backup target escaped the project.' }
                Move-Item -LiteralPath $venv -Destination $backup
                Write-Host "Preserved previous environment: $backup"
            }
            Write-Host "Creating local environment with $base"
            $created = Invoke-Captured $base @('-I', '-B', '-m', 'venv', $venv) 120
            if ($created.Code -ne 0) { Write-Host 'Virtual environment creation failed.'; return 5 }
            Assert-EnvironmentPath $root $venv
            $local = Get-LocalEnvironment $venv
            if (-not $local) { Write-Host 'Created environment did not pass isolation/version checks.'; return 5 }
        }
        catch { Write-Host $_.Exception.Message; return 5 }
    }
    Write-Host 'Installing this project requirements into its local .venv...'
    & $local.Python -I -B -m pip install --disable-pip-version-check --no-input -r $requirements | Out-Host
    if ($LASTEXITCODE -ne 0) { Write-Host 'Dependency installation failed; environment is not ready.'; return 6 }
    if (-not (Test-Dependencies $local.Python $requirements)) { return 7 }
    Write-Host 'Environment is ready.'
    return 0
}

$roots = @($ProjectRoot) + @($Folders)
if ($roots.Count -eq 0) { $roots = @([IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))) }
$exitCode = 0
foreach ($root in $roots) {
    try { $code = Repair-Project $root }
    catch { Write-Host ('Environment operation failed: ' + $_.Exception.Message); $code = 7 }
    if ($code -ne 0 -and $exitCode -eq 0) { $exitCode = $code }
}
if (-not $NoPause) { [void](Read-Host 'Press Enter to close') }
exit $exitCode
