[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$PythonPath = '',
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$Arguments = @('status')
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not $PythonPath) {
    if ($env:CHALLENGE_PYTHON) { $PythonPath = $env:CHALLENGE_PYTHON }
    else {
        $taskBundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
        if (Test-Path -LiteralPath $taskBundledPython) { $PythonPath = $taskBundledPython }
        else { $PythonPath = (Get-Command python -ErrorAction Stop).Source }
    }
}
& $PythonPath -m challenge @Arguments
if ($LASTEXITCODE -ne 0) { throw "Workflow stopped with exit code $LASTEXITCODE. See the structured receipt above." }
