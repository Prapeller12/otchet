[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$LauncherOnedirPath,

    [Parameter(Mandatory = $true)]
    [string]$FrontendDistPath,

    [string]$WebView2RuntimePath,

    [switch]$EvergreenTestBuild,
    [string]$OutputPath,
    [string]$PythonExe = "python"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repositoryRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputPath) {
    $OutputPath = Join-Path $repositoryRoot "dist\portable"
}
$launcherRoot = (Resolve-Path $LauncherOnedirPath).Path
$frontendRoot = (Resolve-Path $FrontendDistPath).Path
$webViewRoot = $null
if (-not $EvergreenTestBuild) {
    if (-not $WebView2RuntimePath) { throw "WebView2RuntimePath is required for a fixed-runtime release." }
    $webViewRoot = (Resolve-Path $WebView2RuntimePath).Path
}
$outputRoot = [System.IO.Path]::GetFullPath($OutputPath)
$distRoot = [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot "dist"))
if (-not $outputRoot.StartsWith($distRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputPath must be a child of the repository dist directory."
}

$launcher = Join-Path $launcherRoot "ReportingSystem.exe"
$pythonDll = Get-ChildItem (Join-Path $launcherRoot "runtime") -Filter "python3*.dll" -File -ErrorAction SilentlyContinue
$pythonLibrary = Join-Path $launcherRoot "runtime\base_library.zip"
$frontendIndex = Join-Path $frontendRoot "index.html"
if (-not (Test-Path $launcher -PathType Leaf)) { throw "A real PyInstaller onedir launcher is required." }
if (-not $pythonDll -or -not (Test-Path $pythonLibrary -PathType Leaf)) { throw "Embedded Python runtime is incomplete." }
if (-not (Test-Path $frontendIndex -PathType Leaf)) { throw "Built frontend index.html is required." }
if (-not $EvergreenTestBuild) {
    $webViewExecutable = Join-Path $webViewRoot "msedgewebview2.exe"
    if (-not (Test-Path $webViewExecutable -PathType Leaf)) { throw "Expanded WebView2 Fixed Runtime is required." }
}

$version = (Get-Content (Join-Path $repositoryRoot "VERSION") -Raw).Trim()
$stage = Join-Path $outputRoot "ReportingSystem"
if (Test-Path $outputRoot) {
    Remove-Item $outputRoot -Recurse -Force
}
New-Item $stage -ItemType Directory -Force | Out-Null

Copy-Item $launcher $stage
Copy-Item (Join-Path $launcherRoot "runtime") (Join-Path $stage "runtime") -Recurse
if (-not $EvergreenTestBuild) {
    Copy-Item $webViewRoot (Join-Path $stage "runtime\webview2") -Recurse
}
New-Item (Join-Path $stage "app\backend") -ItemType Directory -Force | Out-Null
$backendManifest = @{
    application_version = $version
    format_version = 1
    implementation = "pyinstaller-onedir"
} | ConvertTo-Json
Set-Content (Join-Path $stage "app\backend\backend-manifest.json") $backendManifest -Encoding utf8
Copy-Item $frontendRoot (Join-Path $stage "app\frontend") -Recurse
Copy-Item (Join-Path $repositoryRoot "backend\migrations") (Join-Path $stage "app\migrations") -Recurse
Copy-Item (Join-Path $repositoryRoot "config") (Join-Path $stage "config") -Recurse
if ($EvergreenTestBuild) {
    Set-Content (Join-Path $stage "config\app.local.toml") "[webview2]`nruntime_mode = `"evergreen`"" -Encoding utf8
}
Copy-Item (Join-Path $repositoryRoot "resources") (Join-Path $stage "resources") -Recurse
Copy-Item (Join-Path $repositoryRoot "docs") (Join-Path $stage "docs") -Recurse
Copy-Item (Join-Path $repositoryRoot "VERSION") $stage
Copy-Item (Join-Path $PSScriptRoot "templates\start.cmd") $stage
Copy-Item (Join-Path $PSScriptRoot "templates\TESTING.txt") $stage

foreach ($relative in @("data", "attachments", "imports\inbox", "exports", "backups", "temp")) {
    $managedDirectory = Join-Path $stage $relative
    New-Item $managedDirectory -ItemType Directory -Force | Out-Null
    Set-Content (Join-Path $managedDirectory ".portable-dir") "managed by ReportingSystem" -Encoding ascii
}

& $PythonExe (Join-Path $PSScriptRoot "release_manifest.py") create $stage --version $version
if ($LASTEXITCODE -ne 0) { throw "Could not create release manifest." }
& $PythonExe (Join-Path $PSScriptRoot "verify_release.py") $stage
if ($LASTEXITCODE -ne 0) { throw "Staged portable release failed verification." }

$suffix = if ($EvergreenTestBuild) { "-test" } else { "" }
$archive = Join-Path $outputRoot "ReportingSystem-$version-windows-x64$suffix.zip"
Compress-Archive -Path $stage -DestinationPath $archive -CompressionLevel Optimal
$archiveHash = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -Path "$archive.sha256" -Value "$archiveHash  $([System.IO.Path]::GetFileName($archive))" -Encoding ascii
Write-Output $archive
