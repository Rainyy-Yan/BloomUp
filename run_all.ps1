param(
    [string]$PythonPath = 'python',
    [string]$Root = '.ci-state/reproduction'
)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    & $PythonPath -m challenge --root $Root reproduce
    $runExit = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $runExit
