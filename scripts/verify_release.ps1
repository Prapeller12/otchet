[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ReleasePath,

    [string]$PythonExe = "python"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repositoryRoot = Split-Path -Parent $PSScriptRoot
& $PythonExe (Join-Path $PSScriptRoot "verify_release.py") $ReleasePath
if ($LASTEXITCODE -ne 0) {
    throw "Portable release verification failed."
}
