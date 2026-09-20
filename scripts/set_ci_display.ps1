# CI only: make the physical desktop large enough for complete native screenshots.
# No registry update, installation, or change to the portable application.
# Primary API references:
# https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-enumdisplaysettingsw
# https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-changedisplaysettingsw
# https://learn.microsoft.com/windows/win32/api/wingdi/ns-wingdi-devmodew
[CmdletBinding()]
param(
    [ValidateRange(1440, 7680)][int]$Width = 1920,
    [ValidateRange(900, 4320)][int]$Height = 1080
)

$ErrorActionPreference = 'Stop'
if (-not $IsWindows -or $env:GITHUB_ACTIONS -ne 'true') {
    throw 'Display preparation is restricted to the Windows GitHub Actions runner.'
}

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

namespace ReportingCi {
    // Display branch of DEVMODEW's first union; the public structure is 220 bytes.
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct DisplayMode {
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string dmDeviceName;
        public ushort dmSpecVersion, dmDriverVersion, dmSize, dmDriverExtra;
        public uint dmFields;
        public int dmPositionX, dmPositionY;
        public uint dmDisplayOrientation, dmDisplayFixedOutput;
        public short dmColor, dmDuplex, dmYResolution, dmTTOption, dmCollate;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string dmFormName;
        public ushort dmLogPixels;
        public uint dmBitsPerPel, dmPelsWidth, dmPelsHeight, dmDisplayFlags;
        public uint dmDisplayFrequency, dmICMMethod, dmICMIntent, dmMediaType;
        public uint dmDitherType, dmReserved1, dmReserved2, dmPanningWidth, dmPanningHeight;
    }

    public static class Display {
        [DllImport("user32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool EnumDisplaySettingsW(string device, int mode, ref DisplayMode value);

        [DllImport("user32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
        public static extern int ChangeDisplaySettingsW(ref DisplayMode value, uint flags);

        [DllImport("user32.dll", ExactSpelling = true)]
        public static extern int GetSystemMetrics(int index);

        [DllImport("user32.dll", ExactSpelling = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool SetProcessDPIAware();

        [DllImport("user32.dll", ExactSpelling = true)]
        public static extern uint GetDpiForSystem();

        public static DisplayMode NewMode() {
            var value = new DisplayMode();
            int size = Marshal.SizeOf(typeof(DisplayMode));
            if (size != 220) throw new InvalidOperationException("Invalid DEVMODEW layout: " + size);
            value.dmSize = (ushort)size;
            value.dmDriverExtra = 0;
            return value;
        }
    }
}
'@

[void][ReportingCi.Display]::SetProcessDPIAware()
$dpi = [ReportingCi.Display]::GetDpiForSystem()
if ($dpi -ne 96) {
    throw "Native screenshot CI requires 100% display scaling (96 DPI); actual DPI: $dpi."
}

$current = [ReportingCi.Display]::NewMode()
if (-not [ReportingCi.Display]::EnumDisplaySettingsW($null, -1, [ref]$current)) {
    throw 'Cannot read the current physical display mode.'
}
Write-Host "CI display before: $($current.dmPelsWidth)x$($current.dmPelsHeight), DPI $dpi."

if ($current.dmPelsWidth -ne $Width -or $current.dmPelsHeight -ne $Height) {
    $candidate = $null
    $available = [System.Collections.Generic.HashSet[string]]::new()
    for ($index = 0; ; $index++) {
        $mode = [ReportingCi.Display]::NewMode()
        if (-not [ReportingCi.Display]::EnumDisplaySettingsW($null, $index, [ref]$mode)) { break }
        [void]$available.Add("$($mode.dmPelsWidth)x$($mode.dmPelsHeight)")
        if ($mode.dmPelsWidth -eq $Width -and $mode.dmPelsHeight -eq $Height -and $mode.dmBitsPerPel -ge 32) {
            if ($null -eq $candidate -or $mode.dmDisplayFrequency -eq $current.dmDisplayFrequency) {
                $candidate = $mode
            }
        }
    }
    if ($null -eq $candidate) {
        $modes = ($available | Sort-Object) -join ', '
        throw "Display driver does not expose ${Width}x${Height} at 32 bpp. Available: $modes."
    }
    # CDS_TEST checks the driver's exact enumerated mode without applying it.
    $result = [ReportingCi.Display]::ChangeDisplaySettingsW([ref]$candidate, 2)
    if ($result -ne 0) { throw "Display mode test failed with Win32 result $result." }
    # Flags 0: dynamic session change, deliberately no CDS_UPDATEREGISTRY.
    $result = [ReportingCi.Display]::ChangeDisplaySettingsW([ref]$candidate, 0)
    if ($result -ne 0) { throw "Dynamic display mode change failed with Win32 result $result." }
}

$deadline = [DateTime]::UtcNow.AddSeconds(10)
do {
    $actual = [ReportingCi.Display]::NewMode()
    $read = [ReportingCi.Display]::EnumDisplaySettingsW($null, -1, [ref]$actual)
    $screenWidth = [ReportingCi.Display]::GetSystemMetrics(0) # SM_CXSCREEN
    $screenHeight = [ReportingCi.Display]::GetSystemMetrics(1) # SM_CYSCREEN
    if ($read -and $actual.dmPelsWidth -eq $Width -and $actual.dmPelsHeight -eq $Height -and
        $screenWidth -eq $Width -and $screenHeight -eq $Height) {
        Write-Host "CI display verified: ${screenWidth}x${screenHeight}, DPI $dpi."
        return
    }
    Start-Sleep -Milliseconds 100
} while ([DateTime]::UtcNow -lt $deadline)
throw "Physical display did not reach ${Width}x${Height}; actual desktop: ${screenWidth}x${screenHeight}."
