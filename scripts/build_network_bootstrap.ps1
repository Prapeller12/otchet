[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$compiler = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $compiler)) { throw "Windows .NET Framework compiler is unavailable." }
New-Item $OutputDirectory -ItemType Directory -Force | Out-Null
$target = Join-Path $OutputDirectory "ReportingSystemNetwork.exe"
& $compiler /nologo /target:winexe /platform:x64 /optimize+ `
    /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll `
    "/out:$target" (Join-Path $PSScriptRoot "network/NetworkBootstrap.cs")
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $target)) { throw "Native network bootstrap compilation failed." }
