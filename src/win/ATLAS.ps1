<#  ATLAS launcher (Windows).  The ATLAS shortcuts run this file with powershell.exe directly (no Windows Script Host).
      -Mode Launch  (default) start the bridge hidden if needed (never a second one), wait for /api/health,
                    then open the app window - or bring it to the front if it is already open
      -Mode Bridge  start only the bridge
      -Mode Stop    stop the bridge
    The app window is Microsoft Edge (or Chrome) in "app mode": no tabs, no address bar.  #>
param([ValidateSet('Launch','Bridge','Stop')][string]$Mode = 'Launch')
$ErrorActionPreference = 'Stop'

$Root   = Split-Path -Parent $PSScriptRoot                       # ...\ATLAS\app
$Data   = if ($env:ATLAS_HOME) { $env:ATLAS_HOME } else { Join-Path $env:LOCALAPPDATA 'ATLAS' }
$Py     = if ($env:ATLAS_PY) { $env:ATLAS_PY } else { Join-Path $Root '.venv\Scripts\python.exe' }
$Bridge = Join-Path $Root 'bridge\bridge.py'
$Logs   = Join-Path $Data 'logs'
$Port   = 8765
foreach ($c in @((Join-Path $Root 'bridge\config.json'), (Join-Path $Data 'config.json'))) {
    if (Test-Path $c) { try { $j = Get-Content $c -Raw | ConvertFrom-Json; if ($j.port) { $Port = [int]$j.port } } catch { } }
}
if ($env:JARVIS_PORT) { $Port = [int]$env:JARVIS_PORT }
$Url = "http://127.0.0.1:$Port"

function Show-Msg([string]$Text) {
    try { Add-Type -AssemblyName System.Windows.Forms; [void][System.Windows.Forms.MessageBox]::Show($Text, 'ATLAS') } catch { Write-Host $Text }
}
function Test-Bridge {
    try { $r = Invoke-WebRequest -UseBasicParsing -Uri "$Url/api/health" -TimeoutSec 2; return ($r.StatusCode -eq 200) } catch { return $false }
}
function Get-InstalledVersion {
    $m = Join-Path $Root 'bridge\atlas_meta.py'
    if (Test-Path $m) { $t = Get-Content $m -Raw; if ($t -match 'VERSION\s*=\s*"([^"]+)"') { return $Matches[1] } }
    return ''
}
function Get-RunningVersion {
    try { $h = Invoke-RestMethod -UseBasicParsing -Uri "$Url/api/health" -TimeoutSec 3; return [string]$h.version } catch { return $null }
}
# Another bridge already answering on our port may be an OLDER copy (MODI 1.1.x, a hand-started dev copy, a leftover from before  # legacy-modi
# an update). Showing its window would show the old HUD and its old errors, so stop it and start the installed one.
function Stop-OtherBridge([string]$found, [string]$want) {
    Write-Host "A different ATLAS bridge (version $found) is running; this install is $want. Stopping it."
    try { Invoke-WebRequest -UseBasicParsing -Method Post -Uri "$Url/api/quit" -TimeoutSec 3 | Out-Null } catch { }
    for ($i = 0; $i -lt 20; $i++) { if (-not (Test-Bridge)) { return }; Start-Sleep -Milliseconds 500 }
    try {
        Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object { $_.CommandLine -like '*bridge.py*' } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    } catch { }
    for ($i = 0; $i -lt 20; $i++) { if (-not (Test-Bridge)) { return }; Start-Sleep -Milliseconds 500 }
}
function Start-AtlasBridge {
    if (Test-Bridge) {
        $want = Get-InstalledVersion; $found = Get-RunningVersion
        if ((-not $want) -or ($null -eq $found) -or ($found -eq $want)) { return $true }
        Stop-OtherBridge $found $want
        if (Test-Bridge) { Show-Msg "Another program is using ATLAS's port $Port and could not be stopped.`n`nRestart the computer, then open ATLAS again."; return $false }
    }
    if (-not (Test-Path $Py)) { Show-Msg "ATLAS is not installed correctly (Python environment missing).`nRun ATLAS-Setup.bat again."; return $false }
    New-Item -ItemType Directory -Force -Path $Logs | Out-Null
    $out = Join-Path $Logs 'bridge.log'; $err = Join-Path $Logs 'bridge.err.log'
    $p = Start-Process -FilePath $Py -ArgumentList @("`"$Bridge`"") -WorkingDirectory (Join-Path $Root 'bridge') `
         -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
    for ($i = 0; $i -lt 240; $i++) {                               # up to ~2 minutes
        if (Test-Bridge) { return $true }
        if ($p.HasExited) { break }
        Start-Sleep -Milliseconds 500
    }
    $tail = ''
    if (Test-Path $err) { $tail = (Get-Content $err -Tail 8) -join "`n" }
    Show-Msg "ATLAS's bridge did not start.`n`n$tail`n`nLogs: $Logs"
    return $false
}
function Get-AppWindowPid([string]$appdata) {
    # the browser's main process for ATLAS's private profile folder (the one that owns the visible window)
    try {
        $procs = Get-CimInstance Win32_Process -Filter "Name='msedge.exe' OR Name='chrome.exe'" -ErrorAction Stop
        foreach ($p in $procs) {
            $cl = [string]$p.CommandLine
            if ($cl.Contains($appdata) -and -not $cl.Contains('--type=')) {
                $gp = Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue
                if ($gp -and $gp.MainWindowHandle -ne [IntPtr]::Zero) { return $gp }
            }
        }
    } catch { }
    return $null
}
function Show-ExistingWindow([string]$appdata) {
    $gp = Get-AppWindowPid $appdata
    if (-not $gp) { return $false }
    try {
        if (-not ('AtlasWin' -as [type])) {
            Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class AtlasWin {
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
}
'@
        }
        $h = $gp.MainWindowHandle
        if ([AtlasWin]::IsIconic($h)) { [void][AtlasWin]::ShowWindow($h, 9) }   # SW_RESTORE
        [void][AtlasWin]::SetForegroundWindow($h)
        return $true
    } catch {
        try { Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.Interaction]::AppActivate($gp.Id); return $true } catch { return $false }
    }
}
# ---- taskbar identity: without this the Edge/Chrome app window shows a generic globe (or the browser's icon) on the taskbar ----
# Give the window ATLAS's AppUserModelID (the same ID the installer puts on the ATLAS Start Menu / desktop shortcuts) plus a relaunch
# command, name and icon, so Windows groups it under the ATLAS shortcut and shows atlas.ico; also set the window's own icon.
$AtlasAppId = 'ATLAS.CommandInterface'   # must match $AtlasAppId in installer-body.ps1
function Set-AtlasWindowIdentity($gp) {
    $ico = Join-Path $Root 'assets\atlas.ico'
    try {
        if (-not ('AtlasTaskbar' -as [type])) {
            Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class AtlasTaskbar {
    [StructLayout(LayoutKind.Sequential, Pack = 4)] private struct PropKey { public Guid fmtid; public uint pid; }
    [StructLayout(LayoutKind.Explicit)] private struct PropVar { [FieldOffset(0)] public ushort vt; [FieldOffset(8)] public IntPtr p; [FieldOffset(16)] public IntPtr pad; }
    [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")]
    private interface IPropertyStore {
        void GetCount(out uint c);
        void GetAt(uint i, out PropKey k);
        void GetValue(ref PropKey k, out PropVar v);
        void SetValue(ref PropKey k, ref PropVar v);
        void Commit();
    }
    [DllImport("shell32.dll")] private static extern int SHGetPropertyStoreForWindow(IntPtr hwnd, ref Guid iid, [MarshalAs(UnmanagedType.Interface)] out IPropertyStore ps);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern IntPtr LoadImage(IntPtr hinst, string name, uint type, int cx, int cy, uint flags);
    [DllImport("user32.dll")] private static extern IntPtr SendMessage(IntPtr h, uint msg, IntPtr w, IntPtr l);
    private static void Set(IPropertyStore ps, uint pid, string val) {
        PropKey k = new PropKey(); k.fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"); k.pid = pid;
        PropVar v = new PropVar(); v.vt = 31; v.p = Marshal.StringToCoTaskMemUni(val);
        try { ps.SetValue(ref k, ref v); } finally { Marshal.FreeCoTaskMem(v.p); }
    }
    // pids: 2 RelaunchCommand, 3 RelaunchIconResource, 4 RelaunchDisplayNameResource, 5 ID
    public static void Apply(IntPtr hwnd, string appId, string relaunch, string icon, string name) {
        Guid iid = new Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99");
        IPropertyStore ps;
        if (SHGetPropertyStoreForWindow(hwnd, ref iid, out ps) != 0 || ps == null) { return; }
        if (!string.IsNullOrEmpty(icon)) { Set(ps, 3, icon + ",0"); }
        Set(ps, 2, relaunch); Set(ps, 4, name); Set(ps, 5, appId);
        ps.Commit();
        Marshal.ReleaseComObject(ps);
    }
    public static void SetIcon(IntPtr hwnd, string icon) {
        IntPtr big = LoadImage(IntPtr.Zero, icon, 1, 32, 32, 0x10);     // IMAGE_ICON, LR_LOADFROMFILE
        IntPtr small = LoadImage(IntPtr.Zero, icon, 1, 16, 16, 0x10);
        if (big != IntPtr.Zero) { SendMessage(hwnd, 0x80, (IntPtr)1, big); }       // WM_SETICON ICON_BIG
        if (small != IntPtr.Zero) { SendMessage(hwnd, 0x80, IntPtr.Zero, small); }  // WM_SETICON ICON_SMALL
    }
}
'@
        }
        $h = $gp.MainWindowHandle
        if ($h -eq [IntPtr]::Zero) { return $false }
        $ps = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $relaunch = '"' + $ps + '" -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + (Join-Path $PSScriptRoot 'ATLAS.ps1') + '" -Mode Launch'
        if (-not (Test-Path -LiteralPath $ico)) { $ico = '' }
        [AtlasTaskbar]::Apply($h, $AtlasAppId, $relaunch, $ico, 'ATLAS')
        if ($ico) { [AtlasTaskbar]::SetIcon($h, $ico) }
        return $true
    } catch { return $false }
}
function Wait-AtlasWindowIdentity([string]$appdata) {
    # the window appears a moment after the browser starts; give it up to ~20 s, then set it again once the page (favicon) has loaded
    for ($i = 0; $i -lt 40; $i++) {
        $gp = Get-AppWindowPid $appdata
        if ($gp) { [void](Set-AtlasWindowIdentity $gp); Start-Sleep -Seconds 3; [void](Set-AtlasWindowIdentity $gp); return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}
function Find-Browser {
    $cands = @()
    foreach ($exe in @('msedge.exe', 'chrome.exe')) {
        foreach ($hive in @('HKLM', 'HKCU')) {
            try { $v = (Get-ItemProperty "${hive}:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\$exe" -ErrorAction Stop).'(default)'; if ($v) { $cands += $v } } catch { }
        }
    }
    $pf86 = ${env:ProgramFiles(x86)}
    $cands += @("$pf86\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
                "$env:ProgramFiles\Google\Chrome\Application\chrome.exe", "$pf86\Google\Chrome\Application\chrome.exe",
                "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe")
    foreach ($c in $cands) { if ($c -and (Test-Path $c)) { return $c } }
    return $null
}

switch ($Mode) {
    'Stop' {
        try { Invoke-WebRequest -UseBasicParsing -Method Post -Uri "$Url/api/quit" -TimeoutSec 3 | Out-Null } catch { }
        Start-Sleep -Seconds 1
        if (Test-Bridge) {
            Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object { $_.CommandLine -like '*bridge.py*' } |
                ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        }
    }
    'Bridge' {                                                      # used by "start at login"
        $mutex = New-Object System.Threading.Mutex($false, 'Local\ATLAS-Launcher')
        $held = $false
        try { $held = $mutex.WaitOne(150000) } catch [System.Threading.AbandonedMutexException] { $held = $true }
        try { [void](Start-AtlasBridge) } finally { if ($held) { try { $mutex.ReleaseMutex() } catch { } } }
    }
    default {
        $appdata = Join-Path $Data 'appdata'
        # one launch at a time: a second double-click waits here instead of starting a duplicate bridge or window
        $mutex = New-Object System.Threading.Mutex($false, 'Local\ATLAS-Launcher')
        $held = $false
        try { $held = $mutex.WaitOne(150000) } catch [System.Threading.AbandonedMutexException] { $held = $true }
        try {
            if (-not (Start-AtlasBridge)) { exit 1 }
            if (Show-ExistingWindow $appdata) { $gp = Get-AppWindowPid $appdata; if ($gp) { [void](Set-AtlasWindowIdentity $gp) }; exit 0 }
        } finally { if ($held) { try { $mutex.ReleaseMutex() } catch { } } }
        $browser = Find-Browser
        if ($browser) {
            New-Item -ItemType Directory -Force -Path $appdata | Out-Null
            $appArgs = @("--app=$Url", "--user-data-dir=`"$appdata`"", '--window-size=1600,900', '--no-first-run',
                      '--no-default-browser-check', '--autoplay-policy=no-user-gesture-required')
            Start-Process -FilePath $browser -ArgumentList $appArgs
            [void](Wait-AtlasWindowIdentity $appdata)
        } else {
            Start-Process $Url                                       # no Edge/Chrome found: default browser tab
        }
    }
}
