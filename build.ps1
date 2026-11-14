param(
    [string]$PythonPath = "python",
    [switch]$ReadOnlyBuild
)
$ErrorActionPreference = "Stop"
Push-Location -LiteralPath $PSScriptRoot
try {
    $venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    $uvCommand = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue
    if (-not (Test-Path -LiteralPath $venvPython)) {
        if ($uvCommand) {
            & $uvCommand.Source venv --python $PythonPath --no-python-downloads .venv
        } else {
            & $PythonPath -m venv .venv
        }
        if ($LASTEXITCODE -ne 0) { throw "Failed to create Python environment." }
    }
    if ($uvCommand) {
        # uv environments do not include pip unless explicitly seeded.
        & $uvCommand.Source pip install --python $venvPython -r requirements-build.txt
    } else {
        & $venvPython -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('pip') else 1)"
        if ($LASTEXITCODE -ne 0) {
            & $venvPython -m ensurepip --upgrade
            if ($LASTEXITCODE -ne 0) { throw "Failed to prepare pip. Install uv or use a full Python installation." }
        }
        & $venvPython -m pip install -r requirements-build.txt
    }
    if ($LASTEXITCODE -ne 0) { throw "Failed to install build dependencies." }
    $buildArgs = @('build.py')
    if ($ReadOnlyBuild) { $buildArgs += '--read-only' }
    & $venvPython @buildArgs
    if ($LASTEXITCODE -ne 0) { throw "Build failed." }
} finally {
    Pop-Location
}
