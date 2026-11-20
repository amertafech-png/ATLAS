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
function Start-AtlasBridge {
    if (Test-Bridge) { return $true }
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
            if (Show-ExistingWindow $appdata) { exit 0 }
        } finally { if ($held) { try { $mutex.ReleaseMutex() } catch { } } }
        $browser = Find-Browser
        if ($browser) {
            New-Item -ItemType Directory -Force -Path $appdata | Out-Null
            $appArgs = @("--app=$Url", "--user-data-dir=`"$appdata`"", '--window-size=1600,900', '--no-first-run',
                      '--no-default-browser-check', '--autoplay-policy=no-user-gesture-required')
            Start-Process -FilePath $browser -ArgumentList $appArgs
        } else {
            Start-Process $Url                                       # no Edge/Chrome found: default browser tab
        }
    }
}
