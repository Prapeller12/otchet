[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$ReleaseDirectory, [Parameter(Mandatory=$true)][string]$EvidenceDirectory)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$app = (Resolve-Path $ReleaseDirectory).Path
New-Item $EvidenceDirectory -ItemType Directory -Force | Out-Null
$evidence = (Resolve-Path $EvidenceDirectory).Path
$shareName = "OtchetCI" + [Guid]::NewGuid().ToString("N").Substring(0,12)
$unc = "\\127.0.0.1\$shareName"
$mapped = "R:"
$oldLocal = $env:LOCALAPPDATA
$oldProfile = $env:USERPROFILE
$children = @()
function Run-Bootstrap([string]$root, [string]$name, [switch]$Smoke) {
    $report = Join-Path $evidence "$name-bootstrap.json"
    $args = @("--network-bootstrap-report", "`"$report`"")
    if ($Smoke) { $args += @("--network-self-test", "--self-test-report", "`"$(Join-Path $evidence "$name-smoke.json")`"") }
    Write-Host "$( [DateTime]::UtcNow.ToString('O') ) ${name}: starting native UNC client"
    # NoNewWindow selects direct CreateProcess instead of ShellExecute.
    # The WinExe still displays its own preparation and WebView2 windows.
    $process = Start-Process -FilePath (Join-Path $root "ReportingSystemNetwork.exe") -ArgumentList $args -NoNewWindow -PassThru
    Write-Host "$( [DateTime]::UtcNow.ToString('O') ) ${name}: child $($process.Id) started"
    if (-not $process.WaitForExit(360000)) { Stop-Process -InputObject $process -Force; throw "$name bootstrap timed out." }
    if ($process.ExitCode -ne 0 -or -not (Test-Path $report)) { throw "$name bootstrap did not acknowledge a healthy client." }
    $result = Get-Content $report -Raw | ConvertFrom-Json
    if ($result.result -ne "ok" -or $result.executable.StartsWith("\\") -or $result.state.StartsWith("\\")) { throw "Client or database state is not local." }
    if (-not $Smoke) {
        $client = Get-Process -Id $result.child_pid
        $null = $client.Handle
        $script:children += $client
        $windowDeadline = [DateTime]::UtcNow.AddSeconds(5)
        while ($client.MainWindowHandle -eq 0 -and [DateTime]::UtcNow -lt $windowDeadline) { Start-Sleep -Milliseconds 100; $client.Refresh() }
        if ($client.MainWindowHandle -eq 0) { throw "Client has no actual native WebView2 window." }
        Add-Type -AssemblyName System.Drawing
        Add-Type -AssemblyName System.Windows.Forms
        $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
        $bitmap = [System.Drawing.Bitmap]::new($bounds.Width,$bounds.Height)
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        $graphics.CopyFromScreen($bounds.Location,[System.Drawing.Point]::Empty,$bounds.Size)
        $bitmap.Save((Join-Path $evidence "$name-window.png"))
        $graphics.Dispose(); $bitmap.Dispose()
        if (-not $client.CloseMainWindow() -or -not $client.WaitForExit(30000)) { throw "Client failed normal window close." }
    }
    Write-Host "$( [DateTime]::UtcNow.ToString('O') ) ${name}: verified and closed"
    return $result
}
try {
    Write-Host "$( [DateTime]::UtcNow.ToString('O') ) preparing native window observer"
    # Compile before redirected profile variables or the bounded UI deadline.
    Add-Type @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class OtchetNativeError {
 private delegate bool EnumProc(IntPtr hwnd, IntPtr parameter);
 [StructLayout(LayoutKind.Sequential)] private struct Rect { public int Left,Top,Right,Bottom; }
 [DllImport("user32.dll")] private static extern bool EnumWindows(EnumProc callback,IntPtr parameter);
 [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr hwnd,out uint process);
 [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr hwnd);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] private static extern int GetWindowText(IntPtr hwnd,StringBuilder text,int length);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] private static extern int GetClassName(IntPtr hwnd,StringBuilder text,int length);
 [DllImport("user32.dll")] private static extern bool GetWindowRect(IntPtr hwnd,out Rect rect);
 [DllImport("user32.dll",SetLastError=true)] public static extern bool PostMessage(IntPtr hwnd,uint msg,IntPtr w,IntPtr l);
 private static string Text(IntPtr hwnd,bool caption) { var text=new StringBuilder(1024); if(caption) GetWindowText(hwnd,text,text.Capacity); else GetClassName(hwnd,text,text.Capacity); return text.ToString(); }
 public static IntPtr FindDialog(int process,string caption) {
  IntPtr result=IntPtr.Zero;
  EnumWindows((hwnd,parameter)=> { uint owner; GetWindowThreadProcessId(hwnd,out owner);
   if(owner==(uint)process && IsWindowVisible(hwnd) && Text(hwnd,false)=="#32770" && Text(hwnd,true)==caption) { result=hwnd; return false; }
   return true; },IntPtr.Zero);
  return result;
 }
 public static string[] Describe(int process) {
  var result=new List<string>();
  EnumWindows((hwnd,parameter)=> { uint owner; GetWindowThreadProcessId(hwnd,out owner);
   if(owner==(uint)process) { Rect rect; GetWindowRect(hwnd,out rect); result.Add(String.Format("handle={0}; class={1}; title={2}; visible={3}; rect={4},{5},{6},{7}",hwnd,Text(hwnd,false),Text(hwnd,true),IsWindowVisible(hwnd),rect.Left,rect.Top,rect.Right,rect.Bottom)); }
   return true; },IntPtr.Zero);
  return result.ToArray();
 }
}
'@
    Write-Host "$( [DateTime]::UtcNow.ToString('O') ) native window observer compiled"
    $compiler = Join-Path $env:WINDIR "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    $probe = Join-Path $evidence "ReadyMarkerProbe.exe"
    & $compiler /nologo /target:exe /platform:x64 "/out:$probe" (Join-Path $PSScriptRoot "network/ReadyMarkerProbe.cs")
    if ($LASTEXITCODE -ne 0) { throw "Readiness regression probe failed compilation." }
    & $probe (Join-Path $app "ReportingSystemNetwork.exe") $evidence
    if ($LASTEXITCODE -ne 0) { throw "Native readiness sharing/token regression failed." }
    Write-Host "$( [DateTime]::UtcNow.ToString('O') ) creating disposable SMB share"
    New-SmbShare -Name $shareName -Path $app -FullAccess ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) | Out-Null
    Write-Host "$( [DateTime]::UtcNow.ToString('O') ) disposable SMB share created"
    $testShare = Join-Path $app "shared"
    Set-Content (Join-Path $testShare ".otchet-network-self-test") "otchet-disposable-network-self-test-v1" -Encoding ascii -NoNewline
    $smoke = Run-Bootstrap $unc "unc-protocol" -Smoke
    $protocol = Get-Content (Join-Path $evidence "unc-protocol-smoke.json") -Raw | ConvertFrom-Json
    if (-not $protocol.ok) { throw "Packaged encrypted protocol checks failed." }
    foreach ($requiredCheck in @("encrypted_atomic_pair", "two_clients_stale_cas", "idempotent_receipt_replay", "explicit_refresh_guard", "prepublication_rollback", "unknown_outcome_recovery", "unified_draft_unknown_recovery", "two_process_writer_lock")) {
        if ($requiredCheck -notin $protocol.checks) { throw "Missing executable network check: $requiredCheck" }
    }
    $first = Run-Bootstrap $unc "unc-first"
    $second = Run-Bootstrap $unc "unc-warm"
    if ($first.executable -ne $second.executable -or $first.state -ne $second.state) { throw "Warm startup did not reuse verified cache/state." }
    & net.exe use $mapped $unc /persistent:no
    if ($LASTEXITCODE -ne 0) { throw "Mapped drive creation failed." }
    $drive = Run-Bootstrap "$mapped\" "mapped"
    if ($drive.state -ne $first.state -or $drive.executable -ne $first.executable) { throw "Mapped and UNC launch have different identities." }
    $cacheRoot = Split-Path -Parent $first.executable
    Set-Content (Join-Path $cacheRoot "VERSION") "damaged-cache" -Encoding ascii
    $repaired = Run-Bootstrap $unc "damaged-cache"
    if ($repaired.executable -eq $first.executable -or $repaired.state -ne $first.state) { throw "Damaged cache was activated or client state was replaced." }
    if ((Get-Content (Join-Path $cacheRoot "VERSION") -Raw).Trim() -ne "damaged-cache") { throw "Old executable cache was modified/deleted." }
    $repairedWarm = Run-Bootstrap $unc "repaired-warm"
    if ($repairedWarm.executable -ne $repaired.executable -or $repairedWarm.state -ne $repaired.state) { throw "Repaired immutable cache was recopied on the next startup." }
    $env:LOCALAPPDATA = Join-Path $unc "redirected-local"
    $env:USERPROFILE = Join-Path $unc "redirected-profile"
    New-Item $env:LOCALAPPDATA -ItemType Directory -Force | Out-Null
    New-Item (Join-Path $env:USERPROFILE "AppData/Local") -ItemType Directory -Force | Out-Null
    $redirected = Run-Bootstrap $unc "redirected-profile"
    if (-not $redirected.state.StartsWith([System.IO.Path]::GetFullPath($env:TEMP), [System.StringComparison]::OrdinalIgnoreCase)) { throw "Redirected profile did not use actual local TEMP fallback." }
    $override = Join-Path $app "network.local.json"
    @{ shared_directory = "nonexistent-shared-" + [Guid]::NewGuid().ToString("N") } | ConvertTo-Json | Set-Content $override -Encoding utf8
    $errorReport = Join-Path $evidence "unavailable-bootstrap.json"
    Write-Host "$( [DateTime]::UtcNow.ToString('O') ) checking visible native connection error"
    $errorProcess = Start-Process -FilePath (Join-Path $unc "ReportingSystemNetwork.exe") -ArgumentList @("--network-bootstrap-report", "`"$errorReport`"") -NoNewWindow -PassThru
    $script:children += $errorProcess
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    while (-not (Test-Path $errorReport) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 100 }
    if (-not (Test-Path $errorReport)) { Stop-Process -InputObject $errorProcess -Force; throw "Missing visible startup failure report." }
    $failure = Get-Content $errorReport -Raw | ConvertFrom-Json
    if ($failure.result -ne "error" -or $failure.message -notlike "*Общая папка*") { throw "Network failure silently used a local store." }
    Write-Host "$( [DateTime]::UtcNow.ToString('O') ) native error report observed"
    $modal = [IntPtr]::Zero
    $caption = "Не удалось запустить отчётность"
    while ($modal -eq [IntPtr]::Zero -and [DateTime]::UtcNow -lt $deadline) { $modal = [OtchetNativeError]::FindDialog($errorProcess.Id,$caption); Start-Sleep -Milliseconds 100 }
    $observedWindows = [OtchetNativeError]::Describe($errorProcess.Id)
    $observer = @{ ok=($modal -ne [IntPtr]::Zero); process_id=$errorProcess.Id; caption=$caption; dialog_class="#32770"; visible_modal=($modal -ne [IntPtr]::Zero); deadline_utc=$deadline.ToString('O'); observed_utc=[DateTime]::UtcNow.ToString('O'); owned_windows=$observedWindows }
    $observer | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $evidence "native-error-observer.json") -Encoding utf8
    $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    $bitmap = [System.Drawing.Bitmap]::new($bounds.Width,$bounds.Height)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.CopyFromScreen($bounds.Location,[System.Drawing.Point]::Empty,$bounds.Size)
    $captureName = if ($modal -eq [IntPtr]::Zero) { "unavailable-native-error-notfound.png" } else { "unavailable-native-error.png" }
    $bitmap.Save((Join-Path $evidence $captureName)); $graphics.Dispose(); $bitmap.Dispose()
    if ($modal -eq [IntPtr]::Zero) { throw "Startup error had no visible native dialog; owned-window diagnostics captured." }
    Write-Host "$( [DateTime]::UtcNow.ToString('O') ) visible native error dialog observed for child $($errorProcess.Id)"
    if (-not [OtchetNativeError]::PostMessage($modal,0x0010,[IntPtr]::Zero,[IntPtr]::Zero)) { throw "Native error close could not be posted." }
    if (-not $errorProcess.WaitForExit(10000) -or $errorProcess.ExitCode -ne 1) { throw "Native error did not close cleanly." }
    $observer.close_exit_code = $errorProcess.ExitCode
    $observer | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $evidence "native-error-observer.json") -Encoding utf8
    Remove-Item $override
    $summary = @{ ok=$true; os=[Environment]::OSVersion.VersionString; checks=@("encrypted SMB roundtrip", "UNC native window", "verified warm cache", "mapped identity", "redirected-profile local fallback", "corrupt cache rejection", "visible native connection error"); unc=$unc; physical_remote_clients="NOT_RUN"; windows_10_11="NOT_RUN"; distinct_windows_accounts_dpapi="NOT_RUN"; ten_user_load="NOT_RUN" }
    $summary | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $evidence "network-windows-result.json") -Encoding utf8
} finally {
    Write-Host "$( [DateTime]::UtcNow.ToString('O') ) cleaning owned processes and disposable SMB share"
    $env:LOCALAPPDATA = $oldLocal
    $env:USERPROFILE = $oldProfile
    foreach ($ownedChild in $children) {
        if (-not $ownedChild.HasExited) { Stop-Process -InputObject $ownedChild -Force }
    }
    & net.exe use $mapped /delete /y 2>$null
    Remove-SmbShare -Name $shareName -Force -ErrorAction SilentlyContinue
    Write-Host "$( [DateTime]::UtcNow.ToString('O') ) disposable SMB cleanup completed"
}
