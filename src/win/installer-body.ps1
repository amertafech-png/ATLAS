#<<PS1>>
# =====================================================================================
#  MODI-Setup.bat  -  one-file installer for MODI on Windows 10/11 (PowerShell 5.1+)
#  Double-click it.  Windows SmartScreen may say "Windows protected your PC" (the file is
#  not code-signed): click "More info" > "Run anyway".
#  What it does (asks before each big step):
#    1. checks Python 3.11+ (offers to install it with winget)
#    2. installs Hermes Agent with its official installer if it is missing
#    3. runs `hermes setup` so Hermes can sign in to YOUR AI account
#    4. optional: Ollama + local fallback model
#    5. turns on Hermes' local API with a fresh random key (never printed) + restarts the gateway
#    6. installs MODI to %LOCALAPPDATA%\MODI\app, Python packages, voice models
#    7. Desktop + Start Menu shortcuts (own MODI icon, opens as a standalone app window)
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
function New-Shortcut([string]$path, [string]$args, [string]$desc) {
    $ws = New-Object -ComObject WScript.Shell
    $s = $ws.CreateShortcut($path)
    $s.TargetPath = Join-Path $env:WINDIR 'System32\wscript.exe'
    $s.Arguments = $args
    $s.WorkingDirectory = $AppDir
    $s.IconLocation = (Join-Path $AppDir 'assets\modi.ico') + ',0'
    $s.Description = $desc
    $s.Save()
}

if ($ExtractTo) { Extract-Payload $ExtractTo; Write-Host "Extracted to $ExtractTo"; exit 0 }

Write-Host ""
Write-Host "  MODI  -  personal command interface for Hermes Agent  (Windows installer)" -ForegroundColor Cyan

# ---------- uninstall ----------
function Do-Uninstall {
    Step 'Uninstalling MODI'
    if (Stop-Bridge) { Ok 'bridge stopped' }
    foreach ($f in @((Join-Path $Desktop 'MODI.lnk'), (Join-Path $Startup 'MODI bridge.lnk'))) { if (Test-Path $f) { Remove-Item $f -Force; Ok "removed $f" } }
    if (Test-Path $StartMenu) { Remove-Item $StartMenu -Recurse -Force; Ok 'removed Start Menu entries' }
    if (Test-Path $AppDir) { Remove-Item $AppDir -Recurse -Force -ErrorAction SilentlyContinue; Ok "removed $AppDir" }
    if ((Test-Path $Data) -and (Ask "Also delete MODI's saved data (notes, voice and speech models, window profile, logs)?" $false)) {
        Remove-Item $Data -Recurse -Force -ErrorAction SilentlyContinue; Ok "removed $Data"
    }
    Write-Host "  Hermes Agent itself was NOT touched. MODI's API key and API_SERVER_* settings remain in your Hermes config."
    if ((Get-Command hermes -ErrorAction SilentlyContinue) -and (Ask "Turn Hermes' local API server off again?" $false)) {
        hermes config set API_SERVER_ENABLED false | Out-Null; hermes gateway restart | Out-Null; Ok 'Hermes API server disabled'
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
Step '1/7  Checking Python 3.11+'
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

if ($Update) { Step 'Update mode: Hermes setup is left as it is' } else {

# ---------- 2. Hermes ----------
Step '2/7  Hermes Agent'
Refresh-Path
if (-not (Get-Command hermes -ErrorAction SilentlyContinue)) {
    Warn 'Hermes Agent is not installed.'
    if (Ask 'Install it now with the official installer (https://hermes-agent.nousresearch.com/install.ps1)?' $true) {
        try { Invoke-Expression (Invoke-RestMethod -Uri 'https://hermes-agent.nousresearch.com/install.ps1') } catch { Err "Hermes installer failed: $_" }
        Refresh-Path
    }
}
$HermesOk = [bool](Get-Command hermes -ErrorAction SilentlyContinue)
if ($HermesOk) { Ok "hermes: $((Get-Command hermes).Source)" } else { Warn "Hermes CLI not found - MODI will be installed, but run the Hermes installer and 'hermes setup' before using it." }

# ---------- 3. hermes setup ----------
Step '3/7  Sign in to your AI provider (hermes setup)'
if ($HermesOk) {
    if (-not (Test-Path (Join-Path $HermesHome 'config.yaml'))) {
        Warn 'Hermes has not been set up yet.'
        if (Ask "Run 'hermes setup' now (choose your model and sign in with YOUR account)?" $true) { hermes setup }
    } else {
        Ok 'Hermes is already configured'
        if (Ask "Run 'hermes setup' again to change model/login?" $false) { hermes setup }
    }
} else { Warn 'skipped (no Hermes)' }

# ---------- 4. Ollama ----------
Step '4/7  Optional: offline fallback model (Ollama)'
if (Get-Command ollama -ErrorAction SilentlyContinue) { Ok 'Ollama is already installed' }
elseif (Ask 'Install Ollama so Hermes has an offline fallback model?' $false) {
    if (Get-Command winget -ErrorAction SilentlyContinue) { winget install -e --id Ollama.Ollama --accept-package-agreements --accept-source-agreements; Refresh-Path }
    else { Start-Process 'https://ollama.com/download'; Write-Host '  Install Ollama, then run:  ollama pull hermes3:8b' }
}
if (Get-Command ollama -ErrorAction SilentlyContinue) {
    if (-not ((ollama list 2>$null) -match 'hermes3')) {
        if (Ask 'Download the hermes3:8b model now (about 5 GB)?' $false) { ollama pull hermes3:8b }
        Write-Host "  To use it as Hermes' fallback model run:  hermes fallback"
    }
}

# ---------- 5. Hermes API ----------
Step "5/7  Turning on Hermes' local API for MODI (key is not printed)"
if ($HermesOk) {
    $haveKey = $false
    $envFile = Join-Path $HermesHome '.env'
    if (Test-Path $envFile) {
        $line = Get-Content $envFile | Where-Object { $_ -match '^API_SERVER_KEY=' } | Select-Object -Last 1
        if ($line) { $k = ($line -replace '^API_SERVER_KEY=', '').Trim('"', "'", ' '); if ($k.Length -ge 16) { $haveKey = $true } }
    }
    if ($haveKey) { Ok 'API key already present (kept)' }
    else {
        $bytes = New-Object byte[] 24; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        $newKey = 'modi-' + (($bytes | ForEach-Object { $_.ToString('x2') }) -join '')
        hermes config set API_SERVER_KEY $newKey | Out-Null
        if ($LASTEXITCODE -eq 0) { Ok 'fresh random API key written to Hermes config' } else { Err 'could not write API key' }
        $newKey = $null
    }
    hermes config set API_SERVER_ENABLED true | Out-Null; if ($LASTEXITCODE -eq 0) { Ok 'API server enabled' }
    hermes gateway restart | Out-Null
    if ($LASTEXITCODE -eq 0) { Ok 'gateway restarted' }
    else {
        Warn 'gateway was not running - installing it'
        hermes gateway install | Out-Null; hermes gateway start | Out-Null
    }
    Write-Host -NoNewline '  waiting for the API'
    $up = $false
    for ($i = 0; $i -lt 30; $i++) {
        try { $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8642/health' -TimeoutSec 2; if ($r.StatusCode -eq 200) { $up = $true; break } } catch { }
        Write-Host -NoNewline '.'; Start-Sleep -Seconds 1
    }
    Write-Host ''
    if ($up) { Ok 'Hermes API is answering on 127.0.0.1:8642' } else { Warn "Hermes API not answering yet (check: hermes gateway status). MODI will use 'hermes -z' as a fallback." }
} else { Warn 'skipped (no Hermes)' }

}  # end of non-update steps 2-5

# ---------- 6. Install MODI ----------
Step "6/7  Installing MODI to $AppDir"
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

# ---------- 7. Shortcuts ----------
Step '7/7  Shortcuts'
$vbs = '"' + (Join-Path $AppDir 'win\MODI.vbs') + '"'
New-Shortcut (Join-Path $Desktop 'MODI.lnk') $vbs 'MODI - personal command interface for Hermes Agent'
New-Item -ItemType Directory -Force -Path $StartMenu | Out-Null
New-Shortcut (Join-Path $StartMenu 'MODI.lnk') $vbs 'MODI - personal command interface for Hermes Agent'
New-Shortcut (Join-Path $StartMenu 'Stop MODI.lnk') ($vbs + ' stop') 'Stop the MODI bridge'
Ok 'Desktop and Start Menu shortcuts created (pin MODI to the taskbar from the Start Menu if you like)'
$sl = Join-Path $Startup 'MODI bridge.lnk'
if ($Update) {
    if (Test-Path $sl) { New-Shortcut $sl ($vbs + ' bridge') 'MODI bridge at login' }
} elseif (Ask "Start MODI's bridge automatically when you sign in (no window until you open MODI)?" $false) {
    New-Shortcut $sl ($vbs + ' bridge') 'MODI bridge at login'; Ok 'start-at-login enabled'
} elseif (Test-Path $sl) { Remove-Item $sl -Force; Ok 'start-at-login removed' }

if ($Update) {
    Start-Process (Join-Path $env:WINDIR 'System32\wscript.exe') -ArgumentList ($vbs + ' bridge')
    Ok 'MODI updated - the open MODI window reloads by itself'
    Finish 0
}

Write-Host ""
Write-Host " MODI is installed." -ForegroundColor Green
Write-Host "  Open it from the Desktop icon or Start Menu > MODI. It opens as its own window (Edge app mode)."
Write-Host "  First launch: Windows/Edge will ask for Microphone and Camera access - click Allow."
Write-Host "  Uninstall:  run this installer again and choose 2  (or  MODI-Setup.bat uninstall)."
if (Ask 'Launch MODI now?' $true) { Start-Process (Join-Path $env:WINDIR 'System32\wscript.exe') -ArgumentList $vbs }
Finish 0
