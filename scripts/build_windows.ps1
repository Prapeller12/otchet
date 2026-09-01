[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$WebView2RuntimePath,

    [string]$PythonExe = "python",
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not $IsWindows -or -not [Environment]::Is64BitOperatingSystem) {
    throw "Windows x64 is required. PyInstaller cannot produce a verified Windows build on Linux."
}
$repositoryRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repositoryRoot
try {
    $pythonVersion = & $PythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ($pythonVersion.Trim() -ne "3.12") { throw "Python 3.12 x64 is required for the release build." }
    foreach ($dependency in @(
        @{ Name = "pyinstaller"; Version = "6.22.2" },
        @{ Name = "pywebview"; Version = "6.1" }
    )) {
        $actualVersion = & $PythonExe -c "import importlib.metadata as m; print(m.version('$($dependency.Name)'))"
        if ($LASTEXITCODE -ne 0 -or $actualVersion.Trim() -ne $dependency.Version) {
            throw "Build dependency $($dependency.Name)==$($dependency.Version) is required. Install requirements-release.txt."
        }
    }
    if (-not (Test-Path (Join-Path $repositoryRoot "frontend\index.html") -PathType Leaf) -or
        -not (Test-Path (Join-Path $repositoryRoot "vite.config.ts") -PathType Leaf)) {
        throw "The React/Vite frontend is not implemented; a portable release cannot be built."
    }

    & $PythonExe scripts/verify_repository.py
    if ($LASTEXITCODE -ne 0) { throw "Repository verification failed." }
    & $PythonExe -m pytest
    if ($LASTEXITCODE -ne 0) { throw "Backend tests failed." }
    npm ci --ignore-scripts --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw "Frontend dependency installation failed." }
    $frontendDist = Join-Path $repositoryRoot "dist\frontend"
    if (Test-Path $frontendDist) {
        Remove-Item $frontendDist -Recurse -Force
    }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed." }

    & $PythonExe -m PyInstaller --clean --noconfirm scripts/ReportingSystem.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller onedir build failed." }
    & (Join-Path $PSScriptRoot "package_portable.ps1") `
        -LauncherOnedirPath (Join-Path $repositoryRoot "dist\ReportingSystem") `
        -FrontendDistPath (Join-Path $repositoryRoot "dist\frontend") `
        -WebView2RuntimePath $WebView2RuntimePath `
        -OutputPath $OutputPath `
        -PythonExe $PythonExe
} finally {
    Pop-Location
}
