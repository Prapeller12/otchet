[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$Destination)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$pin = Get-Content (Join-Path $PSScriptRoot "webview2-runtime.json") -Raw | ConvertFrom-Json
New-Item $Destination -ItemType Directory -Force | Out-Null
$cab = Join-Path $Destination "runtime.cab"
Invoke-WebRequest -Uri $pin.url -OutFile $cab
if ((Get-FileHash $cab -Algorithm SHA256).Hash.ToLowerInvariant() -ne $pin.sha256) {
    throw "WebView2 CAB SHA-256 does not match the reviewed Microsoft download."
}
$expanded = Join-Path $Destination "expanded"
New-Item $expanded -ItemType Directory -Force | Out-Null
& expand.exe $cab '-F:*' $expanded | Out-Null
if ($LASTEXITCODE -ne 0) { throw "WebView2 CAB extraction failed." }
$executables = @(Get-ChildItem $expanded -Filter msedgewebview2.exe -File -Recurse)
if ($executables.Count -ne 1) { throw "Expected exactly one Fixed Runtime executable." }
$exe = $executables[0]
$signature = Get-AuthenticodeSignature $exe.FullName
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {
    throw "WebView2 executable does not have a valid Microsoft signature."
}
if ($exe.VersionInfo.ProductVersion -ne $pin.version) { throw "Unexpected WebView2 version." }
Write-Output $exe.DirectoryName
