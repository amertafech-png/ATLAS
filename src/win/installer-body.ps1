#<<PS1>>
# =====================================================================================
#  MODI-Setup.bat  -  one-file installer for MODI on Windows 10/11 (PowerShell 5.1+)
#  Double-click it.  Windows SmartScreen may say "Windows protected your PC" (the file is
#  not code-signed): click "More info" > "Run anyway".
#  What it does (asks before each big step):
#    1. checks Python 3.11+ (offers to install it with winget)
#    2. finds Hermes Agent, or installs it with its official installer if you say yes
#    3. installs MODI to %LOCALAPPDATA%\MODI\app, Python packages, voice models
#    4. connects MODI to Hermes (private key, local API, start) - no files to edit, nothing printed
#    5. Desktop + Start Menu shortcuts (own MODI icon, opens as a standalone app window)
#  Signing in to your AI (ChatGPT / Claude / Grok / a local model) happens in MODI's first-run screen.
#  Options: run  MODI-Setup.bat uninstall  to remove MODI;  MODI-Setup.bat /update  for a silent in-place update.
# =====================================================================================
param([string]$Action = '', [switch]$Yes, [switch]$Update, [string]$ExtractTo = '')
# MODI-Setup.bat /update  = non-interactive in-place update (used by MODI's self-update): keeps data + Hermes setup, restarts the bridge
if ($Action -match '^[-/]{1,2}update$') { $Update = $true; $Action = '' }
if ($Update) { $Yes = $true }
$ErrorActionPreference = 'Continue'
function Step($t) { Write-Host ""; Write-Host "==> $t" -ForegroundColor Cyan }
function Ok($t)   { Write-Host "  [ok] $t" -ForegroundColor Green }
function Warn($t) { Write-Host "  [!]  $t" -ForegroundColor Yellow }
function Err($t)  { Write-Host "  [x]  $t" -ForegroundColor Red }
function Ask([string]$q, [bool]$def = $true) {
    if ($Yes) { return $def }
    $hint = if ($def) { '[Y/n]' } else { '[y/N]' }
    $a = (Read-Host "  $q $hint").Trim().ToLower()
    if (-not $a) { return $def }
    return ($a -eq 'y' -or $a -eq 'yes')
}
function Finish([int]$code = 0) { if ((-not $Yes) -or ($Update -and $code -ne 0)) { Write-Host ""; [void](Read-Host "Press Enter to close") }; exit $code }

$Data    = if ($env:MODI_HOME) { $env:MODI_HOME } else { Join-Path $env:LOCALAPPDATA 'MODI' }
$AppDir  = Join-Path $Data 'app'
$HermesHome = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { Join-Path $env:LOCALAPPDATA 'hermes' }
$Desktop = [Environment]::GetFolderPath('Desktop')
$StartMenu = Join-Path ([Environment]::GetFolderPath('Programs')) 'MODI'
$Startup = [Environment]::GetFolderPath('Startup')

function Extract-Payload([string]$dest) {
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    $b64 = ((Get-Content -Raw -LiteralPath $env:MODI_PAYLOAD_FILE) -replace '\s', '')
    $zip = Join-Path $env:TEMP ("modi-payload-" + [guid]::NewGuid().ToString('N') + '.zip')
    [IO.File]::WriteAllBytes($zip, [Convert]::FromBase64String($b64))
    Expand-Archive -Path $zip -DestinationPath $dest -Force
    Remove-Item $zip -Force -ErrorAction SilentlyContinue
}
function Get-Port {
    $p = 8765
    foreach ($c in @((Join-Path $AppDir 'bridge\config.json'), (Join-Path $Data 'config.json'))) {
        if (Test-Path $c) { try { $j = Get-Content $c -Raw | ConvertFrom-Json; if ($j.port) { $p = [int]$j.port } } catch { } }
    }
    if ($env:JARVIS_PORT) { $p = [int]$env:JARVIS_PORT }
    return $p
}
function Stop-Bridge {
    try { Invoke-WebRequest -UseBasicParsing -Method Post -Uri "http://127.0.0.1:$(Get-Port)/api/quit" -TimeoutSec 3 | Out-Null; Start-Sleep -Seconds 1; return $true } catch { return $false }
}
function Find-Python {
    $cands = @()
    foreach ($n in @('python', 'python3')) { $c = Get-Command $n -ErrorAction SilentlyContinue; if ($c -and $c.Source -notlike '*WindowsApps*') { $cands += $c.Source } }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { foreach ($v in @('3.13', '3.12', '3.11')) { try { $p = (& py "-$v" -c 'import sys;print(sys.executable)' 2>$null); if ($p) { $cands += $p } } catch { } } }
    foreach ($d in @("$env:LOCALAPPDATA\Programs\Python", "$env:ProgramFiles\Python313", "$env:ProgramFiles\Python312", "$env:ProgramFiles\Python311")) {
        if (Test-Path $d) { $cands += (Get-ChildItem $d -Filter python.exe -Recurse -Depth 2 -ErrorAction SilentlyContinue | ForEach-Object FullName) }
    }
    foreach ($c in $cands) {
        try { & $c -c 'import sys, venv, ssl; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>$null; if ($LASTEXITCODE -eq 0) { return $c } } catch { }
    }
    return $null
}
function Refresh-Path { $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User') + ";$env:USERPROFILE\.local\bin" }
# Hermes is only *found* here, never configured: MODI connects to it itself (key, API server, start, AI sign-in) in step 4
# and in its first-run screen. The Hermes installer puts `hermes` on the *user* PATH of future terminals only, so look in the known folders too.
function Find-Hermes {
    Refresh-Path
    foreach ($d in @((Join-Path $HermesHome 'bin'), (Join-Path $env:LOCALAPPDATA 'hermes\bin'), (Join-Path $env:USERPROFILE '.local\bin'), (Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps'))) {
        if ((Test-Path $d) -and (($env:Path -split ';') -notcontains $d)) { $env:Path = $env:Path + ';' + $d }
    }
    return [bool](Get-Command hermes -ErrorAction SilentlyContinue)
}
# ---- shortcuts without Windows Script Host: .lnk files are written through the Windows shell-link API (IShellLink) ----
$PsExe = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
function Initialize-LinkApi {
    if ('ModiLnk' -as [type]) { return $true }
    try {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class ModiLnk {
    [ComImport, Guid("00021401-0000-0000-C000-000000000046")] private class ShellLinkCoClass { }
    [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("000214F9-0000-0000-C000-000000000046")]
    private interface IShellLinkW {
        void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszFile, int cch, IntPtr pfd, uint fFlags);
        void GetIDList(out IntPtr ppidl);
        void SetIDList(IntPtr pidl);
        void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszName, int cch);
        void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string pszName);
        void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszDir, int cch);
        void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string pszDir);
        void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszArgs, int cch);
        void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string pszArgs);
        void GetHotkey(out short pwHotkey);
        void SetHotkey(short wHotkey);
        void GetShowCmd(out int piShowCmd);
        void SetShowCmd(int iShowCmd);
        void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszIconPath, int cch, out int piIcon);
        void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string pszIconPath, int iIcon);
        void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string pszPathRel, uint dwReserved);
        void Resolve(IntPtr hwnd, uint fFlags);
        void SetPath([MarshalAs(UnmanagedType.LPWStr)] string pszFile);
    }
    [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("0000010b-0000-0000-C000-000000000046")]
    private interface IPersistFileW {
        void GetClassID(out Guid pClassID);
        [PreserveSig] int IsDirty();
        void Load([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, uint dwMode);
        void Save([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, [MarshalAs(UnmanagedType.Bool)] bool fRemember);
        void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string pszFileName);
        void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string ppszFileName);
    }
    public static void Create(string lnk, string target, string args, string workDir, string icon, string desc, int showCmd) {
        IShellLinkW l = (IShellLinkW)new ShellLinkCoClass();
        l.SetPath(target);
        l.SetArguments(args);
        l.SetWorkingDirectory(workDir);
        l.SetDescription(desc);
        l.SetShowCmd(showCmd);
        if (!string.IsNullOrEmpty(icon)) { l.SetIconLocation(icon, 0); }
        ((IPersistFileW)l).Save(lnk, true);
    }
    public static string[] Read(string lnk) {
        IShellLinkW l = (IShellLinkW)new ShellLinkCoClass();
        ((IPersistFileW)l).Load(lnk, 0);
        StringBuilder t = new StringBuilder(1024);
        StringBuilder a = new StringBuilder(2048);
        StringBuilder d = new StringBuilder(1024);
        StringBuilder i = new StringBuilder(1024);
        int idx;
        l.GetPath(t, t.Capacity, IntPtr.Zero, 0);
        l.GetArguments(a, a.Capacity);
        l.GetDescription(d, d.Capacity);
        l.GetIconLocation(i, i.Capacity, out idx);
        return new string[] { t.ToString(), a.ToString(), d.ToString(), i.ToString() };
    }
}
'@
        return $true
    } catch { return $false }
}
# the command every MODI shortcut runs: PowerShell with no window, straight at MODI.ps1 (-Mode Launch | Bridge | Stop)
function Get-ModiLaunchArgs([string]$mode) {
    return ('-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + (Join-Path $AppDir 'win\MODI.ps1') + '" -Mode ' + $mode)
}
function New-Shortcut([string]$path, [string]$mode, [string]$desc) {
    if (-not (Initialize-LinkApi)) { return $false }
    try {
        [ModiLnk]::Create($path, $PsExe, (Get-ModiLaunchArgs $mode), $AppDir, ((Join-Path $AppDir 'assets\modi.ico') + ',0'), $desc, 7)
        return $true
    } catch { return $false }
}
# plain .cmd launcher, only used when a shortcut could not be written (locked-down PCs)
function New-CmdLauncher([string]$path, [string]$mode) {
    $line = 'start "" /min "' + $PsExe + '" ' + (Get-ModiLaunchArgs $mode)
    Set-Content -LiteralPath $path -Value @('@echo off', $line) -Encoding ASCII
}
# Older MODI versions (<= 1.2.0) pointed every shortcut at wscript.exe + MODI.vbs; some PCs have Windows Script Host switched off.  #legacy-wsh
# Re-point any such shortcut (Desktop, Start Menu, Startup, taskbar pins) and delete the old .vbs launcher.  #legacy-wsh
function Repair-LegacyLaunchers {
    $dirs = @($Desktop, [Environment]::GetFolderPath('CommonDesktopDirectory'), $StartMenu, (Split-Path -Parent $StartMenu), $Startup,
              (Join-Path $env:APPDATA 'Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar'),
              (Join-Path $env:APPDATA 'Microsoft\Internet Explorer\Quick Launch'))
    $fixed = 0
    if (Initialize-LinkApi) {
        foreach ($d in $dirs) {
            if (-not $d -or -not (Test-Path $d)) { continue }
            foreach ($f in (Get-ChildItem -LiteralPath $d -Filter '*.lnk' -File -ErrorAction SilentlyContinue)) {
                try {
                    $i = [ModiLnk]::Read($f.FullName)
                    if (($i[0] -like '*wscript.exe') -and ($i[1] -match 'MODI\.vbs')) {  #legacy-wsh
                        $mode = 'Launch'
                        if ($i[1] -match '(?i)\sstop\s*$') { $mode = 'Stop' } elseif ($i[1] -match '(?i)\sbridge\s*$') { $mode = 'Bridge' }
                        $desc = $i[2]; if (-not $desc) { $desc = 'MODI' }
                        if (New-Shortcut $f.FullName $mode $desc) { $fixed++ }
                    }
                } catch { }
            }
        }
    }
    foreach ($v in @((Join-Path $AppDir 'win\MODI.vbs'), (Join-Path $AppDir 'MODI.vbs'))) { if (Test-Path $v) { Remove-Item $v -Force -ErrorAction SilentlyContinue } }  #legacy-wsh
    if ($fixed -gt 0) { Ok "$fixed old shortcut(s) that used Windows Script Host were switched to the new launcher" }
}

if ($ExtractTo) { Extract-Payload $ExtractTo; Write-Host "Extracted to $ExtractTo"; exit 0 }

Write-Host ""
Write-Host "  MODI  -  personal command interface for Hermes Agent  (Windows installer)" -ForegroundColor Cyan

# ---------- uninstall ----------
function Do-Uninstall {
    Step 'Uninstalling MODI'
    if (Stop-Bridge) { Ok 'bridge stopped' }
    foreach ($f in @((Join-Path $Desktop 'MODI.lnk'), (Join-Path $Desktop 'MODI.cmd'), (Join-Path $Startup 'MODI bridge.lnk'), (Join-Path $Startup 'MODI bridge.cmd'))) { if (Test-Path $f) { Remove-Item $f -Force; Ok "removed $f" } }
    if (Test-Path $StartMenu) { Remove-Item $StartMenu -Recurse -Force; Ok 'removed Start Menu entries' }
    if (Test-Path $AppDir) { Remove-Item $AppDir -Recurse -Force -ErrorAction SilentlyContinue; Ok "removed $AppDir" }
    if ((Test-Path $Data) -and (Ask "Also delete MODI's saved data (notes, voice and speech models, window profile, logs)?" $false)) {
        Remove-Item $Data -Recurse -Force -ErrorAction SilentlyContinue; Ok "removed $Data"
    }
    Write-Host "  Hermes Agent itself was NOT touched. The API key MODI added (API_SERVER_KEY in Hermes' .env) stays, so other tools keep working."
    if ((Find-Hermes) -and (Ask "Turn Hermes' local API server off again?" $false)) {
        hermes config set API_SERVER_ENABLED false | Out-Null; hermes gateway restart --detach --yes | Out-Null; Ok 'Hermes API server disabled'
    }
    Ok 'MODI uninstalled.'
    Finish 0
}
if ($Action -eq 'uninstall') { Do-Uninstall }
if (-not $Yes -and -not $Action) {
    Write-Host ""; Write-Host "  1) Install / update MODI"; Write-Host "  2) Uninstall MODI"; Write-Host "  3) Quit"
    $c = Read-Host "  Choose [1]"
    if ($c -eq '2') { Do-Uninstall }
    if ($c -eq '3') { exit 0 }
}

# ---------- 1. Python ----------
Step '1/5  Checking Python 3.11+'
$Py = $null
if ($Update -and (Test-Path (Join-Path $AppDir '.venv\Scripts\python.exe'))) { Ok 'update mode: using the existing Python environment' }
else { $Py = Find-Python }
if (-not $Py -and -not ($Update -and (Test-Path (Join-Path $AppDir '.venv\Scripts\python.exe')))) {
    Warn 'No Python 3.11 or newer found.'
    if ((Get-Command winget -ErrorAction SilentlyContinue) -and (Ask 'Install Python 3.12 with winget now?' $true)) {
        winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
        Refresh-Path; $Py = Find-Python
    } elseif (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Start-Process 'https://www.python.org/downloads/windows/'
        Write-Host '  Install Python 3.12 (tick "Add python.exe to PATH"), then run this installer again.'
    }
    if (-not $Py) { Err 'Python 3.11+ is not available.'; Finish 1 }
}
if ($Py) { Ok "Python: $Py" }

# ---------- 2. Hermes ----------
Step '2/5  Hermes Agent'
$HermesOk = Find-Hermes
if (-not $HermesOk -and -not $Update) {
    Warn 'Hermes Agent is not installed.'
    if (Ask 'Install it now with the official installer (https://hermes-agent.nousresearch.com/install.ps1)?' $true) {
        try {
            $hi = Join-Path $env:TEMP 'hermes-install.ps1'
            Invoke-WebRequest -UseBasicParsing -Uri 'https://hermes-agent.nousresearch.com/install.ps1' -OutFile $hi
            & (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') -NoProfile -ExecutionPolicy Bypass -File $hi -NonInteractive
            Remove-Item $hi -Force -ErrorAction SilentlyContinue
        } catch { Err "Hermes installer failed: $_" }
        $HermesOk = Find-Hermes
    }
}
if ($HermesOk) { Ok "hermes: $((Get-Command hermes).Source)" }
else { Warn "Hermes is not installed (yet). That's fine - MODI's first screen installs it with one click." }

# ---------- 3. Install MODI ----------
Step "3/5  Installing MODI to $AppDir"
if (Stop-Bridge) {
    for ($i = 0; $i -lt 40; $i++) { try { Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$(Get-Port)/api/health" -TimeoutSec 1 | Out-Null; Start-Sleep -Milliseconds 500 } catch { break } }
}
New-Item -ItemType Directory -Force -Path $AppDir, (Join-Path $Data 'logs') | Out-Null
foreach ($d in @('bridge', 'assets', 'mac', 'win')) { $t = Join-Path $AppDir $d; if (Test-Path $t) { Remove-Item $t -Recurse -Force } }
Remove-Item (Join-Path $AppDir 'index.html') -Force -ErrorAction SilentlyContinue
Extract-Payload $AppDir
Get-ChildItem $AppDir -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue
Ok 'files installed'

$VenvPy = Join-Path $AppDir '.venv\Scripts\python.exe'
if (-not (Test-Path $VenvPy)) { & $Py -m venv (Join-Path $AppDir '.venv'); if ($LASTEXITCODE -ne 0) { Err 'could not create the Python environment'; Finish 1 } }
& $VenvPy -m pip install --quiet --upgrade pip 2>$null | Out-Null
Write-Host '  installing Python packages (aiohttp, faster-whisper, piper-tts, psutil...) - a few minutes the first time'
& $VenvPy -m pip install --quiet -r (Join-Path $AppDir 'bridge\requirements.txt')
if ($LASTEXITCODE -ne 0) { Err 'pip install failed. See the messages above.'; Finish 1 }
Ok 'Python packages installed'
& $VenvPy (Join-Path $AppDir 'bridge\bridge.py') --install-deps
if ($LASTEXITCODE -ne 0) { Warn 'some optional feature packages could not be installed (see Settings in MODI)' }
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    if (Ask 'NVIDIA GPU found. Install CUDA libraries so speech recognition can use it (about 1 GB)? CPU is already fast for the base model.' $false) {
        & $VenvPy -m pip install --quiet nvidia-cublas-cu12 nvidia-cudnn-cu9
        $cfg = Join-Path $Data 'config.json'
        & $VenvPy -c "import json,sys; p=sys.argv[1]; c=json.load(open(p)) if __import__('os').path.exists(p) else {}; c.setdefault('stt',{})['device']='cuda'; json.dump(c,open(p,'w'),indent=2)" $cfg
        Ok 'speech recognition set to use the GPU (stt.device = cuda in config.json)'
    }
}
if (-not $Update -and -not $Yes -and -not ((Test-Path (Join-Path $Data 'config.json')) -and ((Get-Content (Join-Path $Data 'config.json') -Raw) -match '"profile"'))) {
    Write-Host '  A few personal touches (all optional - press Enter to skip; change them later in MODI > Settings):'
    $pAName = Read-Host '  Name your assistant (Enter keeps MODI; 1-20 characters)'
    $pName = Read-Host '  What should your assistant call you?'
    $pCity = Read-Host '  Your city, for weather (e.g. Austin, Texas)'
    $pSports = Read-Host '  Favorite sports, comma separated (Soccer, NFL, NBA, MLB, NHL, College football, F1, or your own)'
    $pa = @((Join-Path $AppDir 'bridge\bridge.py'), '--set-profile')
    if ($pAName) { $pa += @('--assistant', $pAName) }
    if ($pName) { $pa += @('--name', $pName) }
    if ($pCity) { $pa += @('--city', $pCity) }
    foreach ($sp in ($pSports -split ',')) {
        $sp = $sp.Trim()
        if (-not $sp) { continue }
        $lg = ''
        if ($sp -ieq 'soccer') { $lg = (Read-Host "  $sp - leagues (e.g. Premier League, Serie A; Enter to skip)").Trim() }
        $tm = (Read-Host "  $sp - favorite teams, comma separated (Enter to skip)").Trim()
        $pa += @('--sport', "$sp|$lg|$tm")
    }
    if ($pa.Count -gt 2) { & $VenvPy @pa; if ($LASTEXITCODE -ne 0) { Warn 'could not save your profile - set it in MODI > Settings' } }
}
if ((-not $Update) -and (Ask "Download the speech models now (Whisper 'base' ~150 MB + Piper voice ~60 MB)? Otherwise they download on first use." $true)) {
    & $VenvPy (Join-Path $AppDir 'bridge\bridge.py') --prefetch
    if ($LASTEXITCODE -ne 0) { Warn 'model download had problems - MODI will retry on first use' }
}

# ---------- 4. Connect Hermes ----------
Step '4/5  Connecting MODI to Hermes (nothing to type)'
if ($HermesOk) {
    & $VenvPy (Join-Path $AppDir 'bridge\bridge.py') --connect-hermes
    if ($LASTEXITCODE -ne 0) { Warn "Could not finish connecting to Hermes now - MODI's first screen has a 'Fix it' button for that." }
} else { Warn 'skipped - Hermes is not installed yet' }

# ---------- 5. Shortcuts ----------
Step '5/5  Shortcuts'
Repair-LegacyLaunchers
$okAll = $true
$okAll = (New-Shortcut (Join-Path $Desktop 'MODI.lnk') 'Launch' 'MODI - personal command interface for Hermes Agent') -and $okAll
New-Item -ItemType Directory -Force -Path $StartMenu | Out-Null
$okAll = (New-Shortcut (Join-Path $StartMenu 'MODI.lnk') 'Launch' 'MODI - personal command interface for Hermes Agent') -and $okAll
$okAll = (New-Shortcut (Join-Path $StartMenu 'Stop MODI.lnk') 'Stop' 'Stop the MODI bridge') -and $okAll
if ($okAll) { Ok 'Desktop and Start Menu shortcuts created (pin MODI to the taskbar from the Start Menu if you like)' }
else {
    Warn 'Windows would not let this installer create shortcuts - made a MODI.cmd launcher on the Desktop and in the MODI folder instead.'
    New-CmdLauncher (Join-Path $Desktop 'MODI.cmd') 'Launch'
    New-CmdLauncher (Join-Path $StartMenu 'MODI.cmd') 'Launch'
}
$sl = Join-Path $Startup 'MODI bridge.lnk'
if ($Update) {
    if (Test-Path $sl) { [void](New-Shortcut $sl 'Bridge' 'MODI bridge at login') }
} elseif (Ask "Start MODI's bridge automatically when you sign in (no window until you open MODI)?" $false) {
    if (New-Shortcut $sl 'Bridge' 'MODI bridge at login') { Ok 'start-at-login enabled' }
    else { New-CmdLauncher (Join-Path $Startup 'MODI bridge.cmd') 'Bridge'; Ok 'start-at-login enabled (.cmd)' }
} elseif (Test-Path $sl) { Remove-Item $sl -Force; Ok 'start-at-login removed' }

if ($Update) {
    Start-Process -FilePath $PsExe -ArgumentList (Get-ModiLaunchArgs 'Bridge') -WindowStyle Hidden
    Ok 'MODI updated - the open MODI window reloads by itself'
    Finish 0
}

Write-Host ""
Write-Host " MODI is installed." -ForegroundColor Green
Write-Host "  Open it from the Desktop icon or Start Menu > MODI. It opens as its own window (Edge app mode)."
Write-Host "  First launch: Windows/Edge will ask for Microphone and Camera access - click Allow."
Write-Host "  Uninstall:  run this installer again and choose 2  (or  MODI-Setup.bat uninstall)."
if (Ask 'Launch MODI now?' $true) { Start-Process -FilePath $PsExe -ArgumentList (Get-ModiLaunchArgs 'Launch') -WindowStyle Hidden }
Finish 0
