param([switch]$Check, [switch]$NoBrowser, [switch]$Browser, [string]$PythonPath, [string]$EnvironmentRoot)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if ($EnvironmentRoot) { $environmentRoot = [IO.Path]::GetFullPath($EnvironmentRoot) } else { $environmentRoot = Join-Path $env:LOCALAPPDATA 'DataWorkbench\venv' }
$requirements = Join-Path $projectRoot 'requirements.lock.txt'
# The Windows download carries its own Python and dependencies in runtime\python.
# Use it as shipped: no system Python, venv, pip or internet access.
$portableMarker = Join-Path $projectRoot 'runtime\portable.json'
$portable = Test-Path -LiteralPath $portableMarker
if ($portable) {
    $environmentPython = Join-Path $projectRoot 'runtime\python\python.exe'
    if (-not (Test-Path -LiteralPath $environmentPython)) { throw 'The bundled Python is missing. Extract the whole ZIP again into a new folder, then copy your projects folder across.' }
} else {
$environmentPython = Join-Path $environmentRoot 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $environmentPython)) {
    $basePython = $PythonPath
    if (-not $basePython -and $env:DATA_WORKBENCH_PYTHON) { $basePython = $env:DATA_WORKBENCH_PYTHON }
    if (-not $basePython) {
        $pyCommand = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($pyCommand) { $basePython = (& $pyCommand.Source -3 -c 'import sys; print(sys.executable)' | Select-Object -Last 1) }
    }
    if (-not $basePython) {
        $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($pythonCommand -and $pythonCommand.Source -notmatch 'WindowsApps') { $basePython = $pythonCommand.Source }
    }
    if (-not $basePython) {
        foreach ($distribution in @('anaconda3', 'miniconda3')) {
            $candidatePython = Join-Path $env:USERPROFILE ($distribution + '\python.exe')
            if (Test-Path -LiteralPath $candidatePython) { $basePython = $candidatePython; break }
        }
    }
    if (-not $basePython -or -not (Test-Path -LiteralPath $basePython)) { throw 'Install Python 3.12 or newer from python.org, or set DATA_WORKBENCH_PYTHON to an existing Python executable.' }
    & $basePython -c 'import sys; assert sys.version_info >= (3,12), sys.version'
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 or newer is required.' }
    Write-Host 'Setting up Data Workbench in your local app-data folder...'
    & $basePython -m venv $environmentRoot
    if ($LASTEXITCODE -ne 0) { throw 'Python environment setup failed. Install Python 3.12 or newer, then try again.' }
}
$stamp = Join-Path $environmentRoot 'requirements.sha256'
$expectedHash = (& $environmentPython -c 'import hashlib,sys,pathlib; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest().upper())' $requirements | Select-Object -Last 1)
if ($LASTEXITCODE -ne 0) { throw 'Could not verify the dependency lock file.' }
$installedHash = if (Test-Path -LiteralPath $stamp) { (Get-Content -LiteralPath $stamp -Raw).Trim() } else { '' }
if ($expectedHash -ne $installedHash) {
    Write-Host 'Installing the pinned app dependencies (first launch only)...'
    & $environmentPython -m pip install -r $requirements
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check your internet connection and try again.' }
    Set-Content -LiteralPath $stamp -Value $expectedHash -Encoding ASCII
}
}
& $environmentPython -B -c "import fastapi, uvicorn, multipart, duckdb, openpyxl; print('Data Workbench dependencies ready.')"
if ($LASTEXITCODE -ne 0) {
    if ($portable) { throw 'The bundled Python could not load the app. Extract the whole ZIP again into a new folder with a short path, such as C:\DataWorkbench.' }
    throw 'The local environment needs repair. Remove its requirements.sha256 stamp and launch again.'
}
if ($Check) { exit 0 }
$projectsPath = Join-Path $projectRoot 'projects'
# From 3.0 the app opens in its own window (workbench.desktop). pythonw.exe runs it without a
# console, so this window closes once the app has started. -Browser keeps the 2.x behaviour:
# the app opens in the default browser and this window stays open to stop it.
if (-not $Browser -and -not $NoBrowser) {
    $windowPython = Join-Path (Split-Path -Parent $environmentPython) 'pythonw.exe'
    if (-not (Test-Path -LiteralPath $windowPython)) { $windowPython = $environmentPython }
    Start-Process -FilePath $windowPython -WorkingDirectory $projectRoot -ArgumentList @('-B', '-m', 'workbench.desktop', '--projects', ('"' + $projectsPath + '"'))
    Write-Host 'Data Workbench is opening in its own window.'
    exit 0
}
try {
    $existing = Invoke-RestMethod -Uri 'http://127.0.0.1:8810/api/session' -TimeoutSec 2
} catch { $existing = $null }
if ($null -ne $existing) {
    if ($existing.project_root -ne $projectsPath) { throw 'Port 8810 is being used by another workspace. Close that instance first.' }
    Start-Process 'http://127.0.0.1:8810'
    exit 0
}
Set-Location -LiteralPath $projectRoot
Write-Host 'Data Workbench: http://127.0.0.1:8810'
Write-Host 'Keep this window open while working. Press Ctrl+C to stop the app.'
$serverArguments = @('-B', '-m', 'workbench.app', '--projects', $projectsPath, '--port', '8810')
if (-not $NoBrowser) { $serverArguments += '--open-browser' }
& $environmentPython @serverArguments
exit $LASTEXITCODE
