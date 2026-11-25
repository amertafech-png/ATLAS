#<<PS1>>
# =====================================================================================
#  ATLAS-Setup.bat  -  one-file installer for ATLAS on Windows 10/11 (PowerShell 5.1+)
#  Double-click it.  Windows SmartScreen may say "Windows protected your PC" (the file is
#  not code-signed): click "More info" > "Run anyway".
#  What it does (asks before each big step):
#    1. checks Python 3.11+ (offers to install it with winget)
#    2. finds Hermes Agent, or installs it with its official installer if you say yes
#    3. installs ATLAS to %LOCALAPPDATA%\ATLAS\app, Python packages, voice models
#    4. connects ATLAS to Hermes (private key, local API, start) - no files to edit, nothing printed
#    5. Desktop + Start Menu shortcuts (own ATLAS icon, opens as a standalone app window)
#  Signing in to your AI (ChatGPT / Claude / Grok / a local model) happens in ATLAS's first-run screen.
#  Options: run  ATLAS-Setup.bat uninstall  to remove ATLAS;  ATLAS-Setup.bat /update  for a silent in-place update.
# =====================================================================================
param([string]$Action = '', [switch]$Yes, [switch]$Update, [string]$ExtractTo = '')
# ATLAS-Setup.bat /update  = silent in-place update (used by ATLAS's self-update): keeps data + Hermes setup + your earlier choices, restarts the bridge
if ($Action -match '^[-/]{1,2}update$') { $Update = $true; $Action = '' }
if ($Update) { $Yes = $true }
$ErrorActionPreference = 'Continue'
# There are NO console prompts anywhere. Questions are asked on the Options page of the installer window; -Yes / /update never ask (saved or default choices).
function Ok($t)   { Write-Host "  [ok] $t" -ForegroundColor Green;  Ui-Log "[ok] $t" }
function Warn($t) { Write-Host "  [!]  $t" -ForegroundColor Yellow; Ui-Log "[!] $t" }
function Err($t)  { Write-Host "  [x]  $t" -ForegroundColor Red;    Ui-Log "[x] $t" }
function Say($t)  { Write-Host "  $t"; Ui-Log $t }

$Data    = if ($env:ATLAS_HOME) { $env:ATLAS_HOME } else { Join-Path $env:LOCALAPPDATA 'ATLAS' }
$AppDir  = Join-Path $Data 'app'
$HermesHome = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { Join-Path $env:LOCALAPPDATA 'hermes' }
$Desktop = [Environment]::GetFolderPath('Desktop')
$StartMenu = Join-Path ([Environment]::GetFolderPath('Programs')) 'ATLAS'
$Startup = [Environment]::GetFolderPath('Startup')

# ---- no console window behind the installer window (1.4.2) ----
# Double-clicking ATLAS-Setup.bat opens a console. In window (GUI) mode we start a HIDDEN copy of this installer, wait until it reports
# that its window is open, and then this visible copy exits, so the console closes. conhost.exe is used explicitly so the hidden copy
# also stays hidden on PCs where Windows Terminal is the default terminal. If the hidden copy can't open its window, it says so and
# exits, and setup simply continues here in the visible console as before (the old behaviour).
# The hidden copy's console output goes to %TEMP%\ATLAS-setup.log (not under the ATLAS folder: uninstall removes that folder).
$SetupLog = Join-Path $env:TEMP 'ATLAS-setup.log'
$script:HiddenChild = [bool]$env:ATLAS_SETUP_CHILD
function Set-ChildState([string]$state) {
    if ($script:HiddenChild -and $env:ATLAS_SETUP_RUNDIR) { try { Set-Content -LiteralPath (Join-Path $env:ATLAS_SETUP_RUNDIR 'state') -Value $state -Encoding ASCII } catch { } }
}
if ($script:HiddenChild) {
    try { Start-Transcript -LiteralPath $SetupLog -Force | Out-Null } catch { }
    $rd = $env:ATLAS_SETUP_RUNDIR
    if ($rd) { [void](Register-EngineEvent -SourceIdentifier PowerShell.Exiting -Action { try { Stop-Transcript | Out-Null } catch { }; Start-Sleep -Milliseconds 300; Remove-Item -LiteralPath $rd -Recurse -Force -ErrorAction SilentlyContinue }.GetNewClosure()) }
} elseif ((-not $Yes) -and (-not $ExtractTo) -and ($env:OS -eq 'Windows_NT') -and (-not $env:ATLAS_SETUP_NOHIDE) -and (-not $env:ATLAS_UI_NO_OPEN) -and
          $env:ATLAS_PAYLOAD_FILE -and (Test-Path -LiteralPath $env:ATLAS_PAYLOAD_FILE) -and $PSCommandPath) {
    $relaunched = $false; $origPayload = $env:ATLAS_PAYLOAD_FILE; $pl = ''
    try {
        # sweep leftovers of earlier runs, then copy this script + payload to a folder the hidden copy owns (the .bat deletes its own)
        Get-ChildItem -LiteralPath $env:TEMP -Directory -Filter 'atlas-setup-run-*' -ErrorAction SilentlyContinue |
            Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-1) } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        $run = Join-Path $env:TEMP ('atlas-setup-run-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Force -Path $run | Out-Null
        $ps1 = Join-Path $run 'ATLAS-Setup.ps1'; $pl = Join-Path $run 'payload.b64'
        Copy-Item -LiteralPath $PSCommandPath -Destination $ps1 -Force
        Copy-Item -LiteralPath $env:ATLAS_PAYLOAD_FILE -Destination $pl -Force
        $env:ATLAS_SETUP_CHILD = '1'; $env:ATLAS_SETUP_RUNDIR = $run; $env:ATLAS_PAYLOAD_FILE = $pl
        $psExe = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $psArgs = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $ps1 + '"'
        if ($Action) { $psArgs += ' ' + $Action }
        $conhost = Join-Path $env:WINDIR 'System32\conhost.exe'
        if (Test-Path -LiteralPath $conhost) { $child = Start-Process -FilePath $conhost -ArgumentList ('"' + $psExe + '" ' + $psArgs) -WindowStyle Hidden -PassThru }
        else { $child = Start-Process -FilePath $psExe -ArgumentList $psArgs -WindowStyle Hidden -PassThru }
        Write-Host '  Opening the ATLAS Setup window...'
        $deadline = (Get-Date).AddSeconds(120); $state = ''
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Milliseconds 300
            $sf = Join-Path $run 'state'
            if (Test-Path -LiteralPath $sf) { $state = ([string](Get-Content -LiteralPath $sf -Raw)).Trim() }
            if ($state -eq 'ui-up') { $relaunched = $true; break }
            if ($state -eq 'ui-failed' -or $child.HasExited) { break }
        }
        if (-not $relaunched) {
            try { if (-not $child.HasExited) { Stop-Process -Id $child.Id -Force -ErrorAction SilentlyContinue } } catch { }
            Write-Host '  (the setup window did not open from the background - continuing here)'
        }
    } catch { Write-Host ('  (could not start setup in the background: ' + $_.Exception.Message + ' - continuing here)') }
    if ($relaunched) { exit 0 }
    Remove-Item Env:\ATLAS_SETUP_CHILD, Env:\ATLAS_SETUP_RUNDIR -ErrorAction SilentlyContinue
    $env:ATLAS_PAYLOAD_FILE = $origPayload
    if ($pl) { Remove-Item -LiteralPath (Split-Path $pl) -Recurse -Force -ErrorAction SilentlyContinue }
    $env:ATLAS_SETUP_NOHIDE = '1'
}

function Extract-Payload([string]$dest) {
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    $b64 = ((Get-Content -Raw -LiteralPath $env:ATLAS_PAYLOAD_FILE) -replace '\s', '')
    $zip = Join-Path $env:TEMP ("atlas-payload-" + [guid]::NewGuid().ToString('N') + '.zip')
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
# Hermes is only *found* here, never configured: ATLAS connects to it itself (key, API server, start, AI sign-in) in step 4
# and in its first-run screen. The Hermes installer puts `hermes` on the *user* PATH of future terminals only, so look in the known folders too.
function Find-Hermes {
    Refresh-Path
    foreach ($d in @((Join-Path $HermesHome 'bin'), (Join-Path $env:LOCALAPPDATA 'hermes\bin'), (Join-Path $env:USERPROFILE '.local\bin'), (Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps'))) {
        if ((Test-Path $d) -and (($env:Path -split ';') -notcontains $d)) { $env:Path = $env:Path + ';' + $d }
    }
    return [bool](Get-Command hermes -ErrorAction SilentlyContinue)
}
# ---- installer window (no console prompts): a tiny local web page served from this script, shown in an Edge/Chrome app window ----
$script:UiDir = ''        # '' = headless (no window): -Yes, /update, or the window could not start
$script:UiState = [ordered]@{ phase = 'starting'; platform = 'windows'; version = ''; mode = 'install'; steps = @(); status = ''; progress = 0; log = @(); options = @(); installed = $false; installed_version = ''; can_back = $false; blurb = ''; result = $null }
$script:UiSteps = @()     # @{id; label; state; weight}
$script:UiLogLines = New-Object System.Collections.ArrayList
$script:UiPs = $null; $script:UiListener = $null; $script:UiProc = $null
function Ui-Flush {
    if (-not $script:UiDir) { return }
    $script:UiState.steps = @($script:UiSteps | ForEach-Object { [ordered]@{ id = $_.id; label = $_.label; state = $_.state } })
    $n = $script:UiLogLines.Count; $from = [Math]::Max(0, $n - 60)
    $script:UiState.log = @($script:UiLogLines.GetRange($from, $n - $from))
    $json = $script:UiState | ConvertTo-Json -Depth 6 -Compress
    $tmp = Join-Path $script:UiDir 'state.tmp'; $dst = Join-Path $script:UiDir 'state.json'
    try {
        [IO.File]::WriteAllText($tmp, $json, (New-Object Text.UTF8Encoding($false)))
        if (Test-Path $dst) { [IO.File]::Replace($tmp, $dst, $null) } else { [IO.File]::Move($tmp, $dst) }
    } catch { try { Copy-Item -LiteralPath $tmp -Destination $dst -Force } catch { } }
}
function Ui-Log([string]$t) {
    if ([string]::IsNullOrWhiteSpace($t)) { return }
    [void]$script:UiLogLines.Add($t.TrimEnd())
    if ($script:UiDir) { Ui-Flush }
}
function Ui-Plan($list) {
    $script:UiSteps = @($list | ForEach-Object { @{ id = $_[0]; label = $_[1]; weight = $_[2]; state = 'pending' } })
    $script:UiSub = 0.0; Ui-Update-Progress
}
function Ui-Update-Progress {
    $tot = 0.0; $done = 0.0
    foreach ($t in $script:UiSteps) {
        $tot += $t.weight
        if ($t.state -in @('ok', 'skip')) { $done += $t.weight } elseif ($t.state -eq 'run') { $done += $t.weight * $script:UiSub }
    }
    if ($tot -gt 0) { $script:UiState.progress = [Math]::Round(100.0 * $done / $tot, 1) }
}
function Ui-Step([string]$id, [string]$status = '') {
    foreach ($t in $script:UiSteps) {
        if ($t.state -eq 'run') { $t.state = 'ok' }
        if ($t.id -eq $id) { $t.state = 'run'; Write-Host ""; Write-Host "==> $($t.label)" -ForegroundColor Cyan }
    }
    $script:UiSub = 0.0
    if (-not $status) { $status = ($script:UiSteps | Where-Object { $_.id -eq $id } | Select-Object -First 1).label }
    $script:UiState.status = $status; Ui-Update-Progress; Ui-Flush
}
function Ui-Skip([string]$id) { foreach ($t in $script:UiSteps) { if ($t.id -eq $id) { $t.state = 'skip' } }; Ui-Update-Progress; Ui-Flush }
function Ui-Status([string]$t, $sub = $null) {
    Write-Host "  $t"
    $script:UiState.status = $t
    if ($null -ne $sub) { $script:UiSub = [double]$sub }
    Ui-Update-Progress; Ui-Flush
}
function Ui-Fail([string]$id) { foreach ($t in $script:UiSteps) { if ($t.id -eq $id -or $t.state -eq 'run') { $t.state = 'fail' } }; Ui-Flush }
function Ui-Done([bool]$ok, [string]$title, [string]$msg) {
    if ($ok) { foreach ($t in $script:UiSteps) { if ($t.state -in @('run', 'pending')) { $t.state = 'ok' } }; $script:UiState.progress = 100 }
    $script:UiState.phase = 'done'
    $script:UiState.result = [ordered]@{ ok = $ok; title = $title; message = $msg }
    Ui-Flush
}
# run a program, stream its output into the Details log, return its exit code
function Run-Logged([string]$exe, [string[]]$argList) {
    $global:LASTEXITCODE = 0
    & $exe @argList 2>&1 | ForEach-Object { $l = "$_"; if ($l.Trim()) { Write-Host "    $l"; Ui-Log $l } }
    return $LASTEXITCODE
}
function Start-UiServer([string]$dir, [string]$token) {
    $listener = $null; $port = 0
    foreach ($i in 1..60) {
        $port = Get-Random -Minimum 49200 -Maximum 65000
        $l = New-Object System.Net.HttpListener
        $l.Prefixes.Add("http://127.0.0.1:$port/")
        try { $l.Start(); $listener = $l; break } catch { }
    }
    if (-not $listener) { return 0 }
    $rs = [runspacefactory]::CreateRunspace(); $rs.Open()
    $ps = [powershell]::Create(); $ps.Runspace = $rs
    [void]$ps.AddScript({
        param($l, $dir, $token, $port)
        $utf8 = New-Object Text.UTF8Encoding($false)
        while ($l.IsListening) {
            try { $ctx = $l.GetContext() } catch { break }
            $res = $ctx.Response
            try {
                $req = $ctx.Request
                $h = $req.Headers['Host']
                $code = 200; $type = 'application/json'; $bytes = $null
                if ((($h -ne "127.0.0.1:$port") -and ($h -ne "localhost:$port")) -or ($req.QueryString['t'] -ne $token)) { $code = 403; $type = 'text/plain'; $bytes = $utf8.GetBytes('forbidden') }
                else {
                    $path = $req.Url.AbsolutePath
                    if ($req.HttpMethod -eq 'GET' -and $path -eq '/') {
                        $type = 'text/html; charset=utf-8'; $bytes = [IO.File]::ReadAllBytes((Join-Path $dir 'ui.html'))
                    } elseif ($req.HttpMethod -eq 'GET' -and $path -eq '/state') {
                        for ($k = 0; $k -lt 5 -and -not $bytes; $k++) { try { $bytes = [IO.File]::ReadAllBytes((Join-Path $dir 'state.json')) } catch { Start-Sleep -Milliseconds 40 } }
                        if (-not $bytes) { $bytes = $utf8.GetBytes('{"phase":"starting"}') }
                    } elseif ($req.HttpMethod -eq 'POST' -and $path -eq '/answer') {
                        $sr = New-Object IO.StreamReader($req.InputStream, $utf8); $body = $sr.ReadToEnd(); $sr.Close()
                        $tmp = Join-Path $dir 'answer.tmp'; [IO.File]::WriteAllText($tmp, $body, $utf8)
                        $dst = Join-Path $dir 'answer.json'; if (Test-Path $dst) { Remove-Item $dst -Force }
                        [IO.File]::Move($tmp, $dst)
                        $bytes = $utf8.GetBytes('{"ok":true}')
                    } else { $code = 404; $type = 'text/plain'; $bytes = $utf8.GetBytes('no') }
                    [IO.File]::WriteAllText((Join-Path $dir 'seen'), [string][DateTimeOffset]::UtcNow.ToUnixTimeSeconds())
                }
                $res.StatusCode = $code; $res.ContentType = $type; $res.Headers.Add('Cache-Control', 'no-store')
                $res.ContentLength64 = $bytes.Length; $res.OutputStream.Write($bytes, 0, $bytes.Length)
            } catch { }
            finally { try { $res.OutputStream.Close() } catch { } }
        }
    }).AddArgument($listener).AddArgument($dir).AddArgument($token).AddArgument($port)
    [void]$ps.BeginInvoke()
    $script:UiPs = $ps; $script:UiListener = $listener
    return $port
}
function Stop-UiServer {
    try { if ($script:UiListener) { $script:UiListener.Stop(); $script:UiListener.Close() } } catch { }
    try { if ($script:UiPs) { $script:UiPs.Stop(); $script:UiPs.Runspace.Close() } } catch { }
    try { if ($script:UiProc -and -not $script:UiProc.HasExited) { [void]$script:UiProc.CloseMainWindow() } } catch { }
}
# web links (python.org etc.) go to the user's DEFAULT browser: Start-Process <url> = ShellExecute "open" on the https handler,
# never msedge.exe/chrome.exe directly (those are only used for the installer's own local app window below)
function Open-DefaultBrowser([string]$url) {
    if ($url -notmatch '^https?://[^\s"]+$') { return $false }
    try { Start-Process $url; return $true } catch { }
    try { Start-Process -FilePath (Join-Path $env:WINDIR 'System32\rundll32.exe') -ArgumentList @('url.dll,FileProtocolHandler', $url); return $true } catch { return $false }
}
# ---- the app window's browser ----
# Order: the user's DEFAULT browser if it is Chromium-based (Chrome / Edge / Brave), else Chrome, else Edge, else Brave.
# Chrome is preferred over Edge because Edge signs a new profile in with the Windows Microsoft account ("implicit sign-in") and
# shows "We are now syncing your browsing data ... we've also signed you in to Microsoft Edge" in the ATLAS window.
function Get-DefaultBrowserKind {
    foreach ($proto in @('https', 'http')) {
        try {
            $id = (Get-ItemProperty "HKCU:\Software\Microsoft\Windows\Shell\Associations\UrlAssociations\$proto\UserChoice" -ErrorAction Stop).ProgId
            if ($id -like 'ChromeHTML*') { return 'chrome' }
            if ($id -like 'MSEdgeHTM*') { return 'msedge' }
            if ($id -like 'BraveHTML*') { return 'brave' }
            if ($id) { return '' }                                 # Firefox etc.: not usable for an app window
        } catch { }
    }
    return ''
}
function Find-BrowserExe([string]$kind) {
    $exe = "$kind.exe"
    $cands = @()
    foreach ($hive in @('HKCU', 'HKLM')) {
        try { $v = (Get-ItemProperty "${hive}:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\$exe" -ErrorAction Stop).'(default)'; if ($v) { $cands += $v.Trim('"') } } catch { }
    }
    $pf86 = ${env:ProgramFiles(x86)}
    switch ($kind) {
        'chrome' { $cands += @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe", "$pf86\Google\Chrome\Application\chrome.exe", "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe") }
        'msedge' { $cands += @("$pf86\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") }
        'brave'  { $cands += @("$env:ProgramFiles\BraveSoftware\Brave-Browser\Application\brave.exe", "$env:LOCALAPPDATA\BraveSoftware\Brave-Browser\Application\brave.exe") }
    }
    foreach ($c in $cands) { if ($c -and (Test-Path -LiteralPath $c)) { return $c } }
    return $null
}
function Find-Browser {
    $order = @()
    $def = Get-DefaultBrowserKind
    if ($def) { $order += $def }
    foreach ($k in @('chrome', 'msedge', 'brave')) { if ($order -notcontains $k) { $order += $k } }
    foreach ($k in $order) { $p = Find-BrowserExe $k; if ($p) { return @{ Path = $p; Kind = $k } } }
    return $null
}
# Switches for ATLAS's app windows (same as win\\ATLAS.ps1; tools/check-app-window.sh checks they match).
# A private profile (never the user's own), no first-run / default-browser / sign-in prompts, no sync, nothing left running in the
# background. Edge only: switch off its implicit Microsoft-account sign-in (the sync popup) - --disable-sync alone does not.
# Chromium only honours ONE --disable-features switch, so all features go in one.
function Get-AppWindowArgs([string]$kind, [string]$url, [string]$profileDir, [string]$size, [bool]$fullscreen = $false) {
    $a = @("--app=$url", "--user-data-dir=`"$profileDir`"", "--window-size=$size", '--no-first-run', '--no-default-browser-check',
           '--disable-sync', '--disable-signin-promo', '--disable-background-mode', '--no-service-autorun', '--autoplay-policy=no-user-gesture-required',
           '--disable-session-crashed-bubble', '--hide-crash-restore-bubble')
    # --kiosk is real full screen (no title bar; Windows hides the taskbar). The installer leaves $fullscreen false.
    # Links are not opened in this window. One --disable-features only, and only for Edge, after this.
    if ($fullscreen) { $a += '--kiosk' }
    if ($kind -eq 'msedge') { $a += '--disable-features=msImplicitSignin,msEdgeImplicitSignin,msIdentityImplicitSignin,msEdgeSyncConsent' }
    return $a
}
function Open-UiWindow([string]$url, [string]$profileDir) {
    # the installer's own window: an app window with a throwaway private profile (never the user's browser profile)
    $b = Find-Browser
    if ($b) {
        try {
            New-Item -ItemType Directory -Force -Path (Join-Path $profileDir 'Default') | Out-Null
            $prefs = '{"signin":{"allowed":false,"allowed_on_next_startup":false},"sync":{"requested":false},"browser":{"has_seen_welcome_page":true,"check_default_browser":false}}'
            try { [IO.File]::WriteAllText((Join-Path $profileDir 'Default\Preferences'), $prefs, (New-Object System.Text.UTF8Encoding($false))) } catch { }
            $script:UiProc = Start-Process -FilePath $b.Path -ArgumentList (Get-AppWindowArgs $b.Kind $url $profileDir '880,760') -PassThru
            return $true
        } catch { }
    }
    return (Open-DefaultBrowser $url)
}
function Read-Answer {
    $f = Join-Path $script:UiDir 'answer.json'
    if (-not (Test-Path $f)) { return $null }
    try { $j = Get-Content -Raw -LiteralPath $f | ConvertFrom-Json; Remove-Item $f -Force -ErrorAction SilentlyContinue; return $j } catch { return $null }
}
# wait for the user's next click; $null = the window was closed / nobody answered
function Wait-Answer([int]$maxSeconds = 1800) {
    $t0 = Get-Date
    while (((Get-Date) - $t0).TotalSeconds -lt $maxSeconds) {
        $a = Read-Answer
        if ($a) { return $a }
        $seen = Join-Path $script:UiDir 'seen'
        if (Test-Path $seen) { if (((Get-Date) - (Get-Item $seen).LastWriteTime).TotalSeconds -gt 25) { return $null } }
        elseif (((Get-Date) - $t0).TotalSeconds -gt 120) { return $null }
        Start-Sleep -Milliseconds 250
    }
    return $null
}
$script:UiSub = 0.0
# ---- shortcuts without Windows Script Host: .lnk files are written through the Windows shell-link API (IShellLink) ----
$PsExe = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
$AtlasAppId = 'ATLAS.CommandInterface'   # Windows AppUserModelID; must match $AtlasAppId in win\ATLAS.ps1
function Initialize-LinkApi {
    if ('AtlasLnk' -as [type]) { return $true }
    try {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class AtlasLnk {
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
    // System.AppUserModel.ID: the ATLAS window sets the same ID (ATLAS.ps1), so Windows groups it under this shortcut and shows its icon
    private static void SetAppId(object link, string appId) {
        IPropertyStore ps = (IPropertyStore)link;
        PropKey k = new PropKey(); k.fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"); k.pid = 5;
        PropVar v = new PropVar(); v.vt = 31; v.p = Marshal.StringToCoTaskMemUni(appId);
        try { ps.SetValue(ref k, ref v); ps.Commit(); } finally { Marshal.FreeCoTaskMem(v.p); }
    }
    [DllImport("shell32.dll")] private static extern void SHChangeNotify(int wEventId, uint uFlags, IntPtr dwItem1, IntPtr dwItem2);
    // tell Explorer that icons changed (SHCNE_ASSOCCHANGED) so shortcuts that showed a blank page get redrawn
    public static void Refresh() { SHChangeNotify(0x08000000, 0, IntPtr.Zero, IntPtr.Zero); }
    public static void Create(string lnk, string target, string args, string workDir, string icon, string desc, int showCmd, string appId) {
        IShellLinkW l = (IShellLinkW)new ShellLinkCoClass();
        l.SetPath(target);
        l.SetArguments(args);
        l.SetWorkingDirectory(workDir);
        l.SetDescription(desc);
        l.SetShowCmd(showCmd);
        if (!string.IsNullOrEmpty(icon)) { l.SetIconLocation(icon, 0); }
        if (!string.IsNullOrEmpty(appId)) { try { SetAppId(l, appId); } catch { } }
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
# the command every ATLAS shortcut runs: PowerShell with no window, straight at ATLAS.ps1 (-Mode Launch | Bridge | Stop)
function Get-AtlasLaunchArgs([string]$mode) {
    return ('-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + (Join-Path $AppDir 'win\ATLAS.ps1') + '" -Mode ' + $mode)
}
function New-Shortcut([string]$path, [string]$mode, [string]$desc) {
    if (-not (Initialize-LinkApi)) { return $false }
    try {
        # IconLocation = the installed atlas.ico (absolute path, no ",0" glued on - SetIconLocation takes the index as its own argument; icon index 0)
        $ico = Join-Path $AppDir 'assets\atlas.ico'
        if (-not (Test-Path -LiteralPath $ico)) { $ico = '' }
        # the app shortcuts carry ATLAS's AppUserModelID (same as the window, see ATLAS.ps1) so the taskbar shows the ATLAS icon, not a globe
        $appId = $(if ($mode -eq 'Launch') { $AtlasAppId } else { '' })
        [AtlasLnk]::Create($path, $PsExe, (Get-AtlasLaunchArgs $mode), $AppDir, $ico, $desc, 7, $appId)
        return $true
    } catch { return $false }
}
# make Explorer redraw shortcut icons now (old shortcuts and the icon cache may still show a blank page)
function Refresh-IconCache {
    try { if (Initialize-LinkApi) { [AtlasLnk]::Refresh() } } catch { }
    try {
        $ie = Join-Path $env:WINDIR 'System32\ie4uinit.exe'
        if (Test-Path $ie) { $p = Start-Process -FilePath $ie -ArgumentList '-show' -WindowStyle Hidden -PassThru; [void]$p.WaitForExit(8000) }
    } catch { }
}
# plain .cmd launcher, only used when a shortcut could not be written (locked-down PCs)
function New-CmdLauncher([string]$path, [string]$mode) {
    $line = 'start "" /min "' + $PsExe + '" ' + (Get-AtlasLaunchArgs $mode)
    Set-Content -LiteralPath $path -Value @('@echo off', $line) -Encoding ASCII
}
# ---- upgrade from MODI (v1.1.x and older; the product is now called ATLAS) ----   #legacy-modi
# Those versions kept everything under %LOCALAPPDATA%\MODI and their shortcuts pointed at wscript.exe + MODI.vbs (Windows Script Host,   # legacy-modi #legacy-wsh
# which many PCs have switched off).   #legacy-modi
$OldData = if ($env:MODI_HOME) { $env:MODI_HOME } else { Join-Path $env:LOCALAPPDATA 'MODI' }   #legacy-modi
$OldStartMenu = Join-Path ([Environment]::GetFolderPath('Programs')) 'MODI'   #legacy-modi
$script:MigratedStartup = $false
function Get-LinkDirs {
    return @($Desktop, [Environment]::GetFolderPath('CommonDesktopDirectory'), (Split-Path -Parent $StartMenu), $StartMenu, $Startup,
             (Join-Path $env:APPDATA 'Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar'),
             (Join-Path $env:APPDATA 'Microsoft\Internet Explorer\Quick Launch'))
}
function Test-LegacyLink($i) {
    # a shortcut written by MODI <= 1.1.x / 1.2 betas: wscript + MODI.vbs, or powershell + ...\MODI\app\win\MODI.ps1   # legacy-modi #legacy-wsh
    return (($i[1] -match 'MODI\.vbs') -or ($i[1] -match '\\MODI\\app\\win\\MODI\.ps1'))   #legacy-modi  #legacy-wsh
}
function Move-LegacyData([string]$from, [string]$to) {
    if (-not (Test-Path -LiteralPath $from)) { return $true }
    if ((Test-Path -LiteralPath $to) -and (Get-ChildItem -LiteralPath $to -Force -ErrorAction SilentlyContinue | Select-Object -First 1)) {
        # ATLAS data exists already: copy over whatever is missing, leave the old folder
        Get-ChildItem -LiteralPath $from -Force | Where-Object { $_.Name -ne 'app' } | ForEach-Object {
            $t = Join-Path $to $_.Name
            if (-not (Test-Path -LiteralPath $t)) { Copy-Item -LiteralPath $_.FullName -Destination $t -Recurse -Force -ErrorAction SilentlyContinue }
        }
        return $false
    }
    if (Test-Path -LiteralPath $to) { Remove-Item -LiteralPath $to -Force -ErrorAction SilentlyContinue }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $to) | Out-Null
    try { Move-Item -LiteralPath $from -Destination $to -ErrorAction Stop; return $true }
    catch {                                                     # something still holds a file: copy, then delete what we can
        Copy-Item -LiteralPath $from -Destination $to -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $from -Recurse -Force -ErrorAction SilentlyContinue
        return (Test-Path -LiteralPath $to)
    }
}
function Migrate-FromOldName {
    $oldLinks = @((Join-Path $Desktop 'MODI.lnk'), (Join-Path $Startup 'MODI bridge.lnk'))   #legacy-modi
    $has = (Test-Path -LiteralPath $OldData) -or (Test-Path -LiteralPath $OldStartMenu) -or ($oldLinks | Where-Object { Test-Path -LiteralPath $_ })
    if (-not $has -or ($OldData -eq $Data)) { return }
    Say 'Upgrading from MODI: ATLAS keeps your settings, notes and voices'   # legacy-modi
    # 1. stop the old bridge (same port as before) and anything still running from the old folder
    $op = 8765
    $oc = Join-Path $OldData 'config.json'
    if (Test-Path $oc) { try { $j = Get-Content $oc -Raw | ConvertFrom-Json; if ($j.port) { $op = [int]$j.port } } catch { } }
    if ($env:JARVIS_PORT) { $op = [int]$env:JARVIS_PORT }
    try { Invoke-WebRequest -UseBasicParsing -Method Post -Uri "http://127.0.0.1:$op/api/quit" -TimeoutSec 3 | Out-Null } catch { }
    for ($i = 0; $i -lt 30; $i++) { try { Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$op/api/health" -TimeoutSec 1 | Out-Null; Start-Sleep -Milliseconds 500 } catch { break } }
    try {
        Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($OldData + '\', [StringComparison]::OrdinalIgnoreCase) } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    } catch { }
    Start-Sleep -Milliseconds 800
    # 2. old shortcuts: remove the standard ones; re-point any other (taskbar pin, custom copy) at ATLAS
    if (Test-Path -LiteralPath (Join-Path $Startup 'MODI bridge.lnk')) { $script:MigratedStartup = $true }   #legacy-modi
    foreach ($f in $oldLinks) { if (Test-Path -LiteralPath $f) { Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue; Ok "removed old shortcut $f" } }
    if (Test-Path -LiteralPath $OldStartMenu) { Remove-Item -LiteralPath $OldStartMenu -Recurse -Force -ErrorAction SilentlyContinue; Ok 'removed the old Start Menu folder' }
    # 3. data (config, notes, voices, speech models, window profile, logs)
    if (Test-Path -LiteralPath $OldData) {
        if (Move-LegacyData $OldData $Data) { Ok "your data moved to $Data" }
        else { Warn "ATLAS data already existed; copied what was missing. The old folder $OldData can be deleted when you are happy." }
    }
}
# Windows Script Host launchers from earlier builds and shortcuts that still point at the old name: re-point them (taskbar pins keep working)
function Repair-LegacyLaunchers {
    $fixed = 0
    if (Initialize-LinkApi) {
        foreach ($d in (Get-LinkDirs)) {
            if (-not $d -or -not (Test-Path $d)) { continue }
            foreach ($f in (Get-ChildItem -LiteralPath $d -Filter '*.lnk' -File -ErrorAction SilentlyContinue)) {
                try {
                    $i = [AtlasLnk]::Read($f.FullName)
                    if (Test-LegacyLink $i) {
                        $mode = 'Launch'
                        if ($i[1] -match '(?i)\sstop\s*$') { $mode = 'Stop' } elseif ($i[1] -match '(?i)\sbridge\s*$') { $mode = 'Bridge' }
                        $desc = $i[2]; if (-not $desc) { $desc = 'ATLAS' }
                        if (New-Shortcut $f.FullName $mode $desc) { $fixed++ }
                    }
                } catch { }
            }
        }
    }
    foreach ($v in @((Join-Path $AppDir 'win\MODI.vbs'), (Join-Path $AppDir 'MODI.vbs'), (Join-Path $AppDir 'win\MODI.ps1'))) { if (Test-Path $v) { Remove-Item $v -Force -ErrorAction SilentlyContinue } }   #legacy-modi  #legacy-wsh
    if ($fixed -gt 0) { Ok "$fixed old shortcut(s) were switched to the ATLAS launcher" }
}

if ($ExtractTo) { Extract-Payload $ExtractTo; Write-Host "Extracted to $ExtractTo"; exit 0 }

$AtlasVersion = ''
$Stage = ''            # the installer's own files, unpacked once (also holds the window's page)
$UiRoot = ''
$OptFile = Join-Path $Data 'install-options.json'
$script:Failed = ''

function Get-InstalledVersion {
    foreach ($m in @((Join-Path $AppDir 'bridge\atlas_meta.py'), (Join-Path $OldData 'app\bridge\modi_meta.py'))) {   # legacy-modi
        if (Test-Path $m) { $x = [regex]::Match((Get-Content $m -Raw), 'VERSION\s*=\s*"([^"]+)"'); if ($x.Success) { return $x.Groups[1].Value } }
    }
    return ''
}
function Test-Installed { return ((Test-Path (Join-Path $AppDir 'bridge\bridge.py')) -or (Test-Path (Join-Path $OldData 'app\bridge\bridge.py'))) }   # legacy-modi
function Read-SavedOptions {
    $o = @{}
    if (Test-Path $OptFile) { try { (Get-Content $OptFile -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $o[$_.Name] = [bool]$_.Value } } catch { } }
    return $o
}
# every question the old console installer asked, now one row on the Options page: id, group, label, one-line explanation, download size, default
function Get-OptionDefs([string]$mode) {
    $saved = Read-SavedOptions
    $defs = @()
    if ($mode -eq 'uninstall') {
        $defs += @{ id = 'delete_data'; group = 'Uninstall'; label = "Also delete ATLAS's saved data"; desc = 'Notes, settings, voice and speech models, window profile and logs. Off = keep them in case you reinstall.'; size = ''; value = $false }
        if (Find-Hermes) { $defs += @{ id = 'hermes_api_off'; group = 'Uninstall'; label = "Turn Hermes' local API server off again"; desc = 'Hermes itself is never removed. Leave this off if other tools use its API.'; size = ''; value = $false } }
        return $defs
    }
    $needPy = -not (Find-Python)
    if ($needPy -and -not (Test-Path (Join-Path $AppDir '.venv\Scripts\python.exe'))) {
        $hasWinget = [bool](Get-Command winget -ErrorAction SilentlyContinue)
        $defs += @{ id = 'python'; group = 'Needed'; label = 'Python 3.12 (required)'; desc = $(if ($hasWinget) { 'ATLAS runs on Python and it is not on this computer, so setup adds it with winget (from python.org).' } else { 'ATLAS needs Python 3.11+ and winget is not available: setup will open python.org so you can install it, then you run Setup again.' }); size = '~30 MB'; value = $true; disabled = $true }
    }
    if (-not (Find-Hermes)) {
        $defs += @{ id = 'hermes'; group = 'AI'; label = 'Install Hermes Agent'; desc = "The AI engine ATLAS talks to (official installer from hermes-agent.nousresearch.com). Skip it if you will install Hermes yourself."; size = '~1 GB'; value = $true }
    }
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue) -and -not (Test-Path (Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'))) {
        $defs += @{ id = 'ollama'; group = 'AI'; label = 'Install Ollama for local models'; desc = 'Only if you want an AI that runs on this computer instead of ChatGPT/Claude/Grok (official installer from ollama.com; models are downloaded later, only when you pick one).'; size = '~1.9 GB'; value = $false }
    }
    $defs += @{ id = 'speech'; group = 'Voice'; label = 'Download the speech models now'; desc = 'Whisper (hearing you) and a Piper voice (speaking). Off = they download the first time you talk to ATLAS.'; size = '~210 MB'; value = $true }
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        $defs += @{ id = 'cuda'; group = 'Voice'; label = 'NVIDIA CUDA libraries for faster voice'; desc = 'Lets speech recognition use your NVIDIA graphics card. The CPU is already fast for the normal model.'; size = '~1 GB'; value = $false }
    }
    $defs += @{ id = 'wake'; group = 'Voice'; label = 'Download the "Hey ATLAS" wake-word model now'; desc = 'Optional hands-free wake word. This only downloads it; you switch it on later in Settings > Features.'; size = '~55 MB'; value = $false }
    $defs += @{ id = 'desktop'; group = 'Shortcuts'; label = 'Desktop shortcut'; desc = 'An ATLAS icon on your Desktop. (The Start Menu entry is always added.)'; size = ''; value = $true }
    $defs += @{ id = 'login'; group = 'Shortcuts'; label = 'Start ATLAS when I sign in'; desc = 'Starts the background part only, so ATLAS is ready when you open it. No window appears by itself.'; size = ''; value = $true }
    foreach ($d in $defs) { if ($saved.ContainsKey($d.id) -and -not $d.disabled) { $d.value = $saved[$d.id] } }
    return $defs
}
function Get-OptionValues($defs, $answer) {
    $o = @{}
    foreach ($d in $defs) { $o[$d.id] = [bool]$d.value }
    if ($answer -and $answer.PSObject.Properties) { foreach ($p in $answer.PSObject.Properties) { if ($o.ContainsKey($p.Name)) { $o[$p.Name] = [bool]$p.Value } } }
    return $o
}
# -Update keeps what the user chose last time; an install from before this file existed is read off the machine
function Get-UpdateOptions($defs) {
    $o = Get-OptionValues $defs $null
    if (-not (Test-Path $OptFile)) {
        $o['login'] = (Test-Path (Join-Path $Startup 'ATLAS bridge.lnk')) -or (Test-Path (Join-Path $Startup 'ATLAS bridge.cmd')) -or $script:MigratedStartup
        $o['desktop'] = $true
        $o['speech'] = $false; $o['wake'] = $false; $o['ollama'] = $false; $o['cuda'] = $false
    } else { $o['speech'] = $false; $o['wake'] = $false; $o['ollama'] = $false }   # downloads are not repeated on an update
    return $o
}

function Fail-Install([string]$step, [string]$msg) {
    Err $msg
    Ui-Fail $step
    $script:Failed = $msg
    throw 'ATLAS-INSTALL-FAILED'
}

function Run-Uninstall($o) {
    Ui-Plan @(@('stop', 'Stop ATLAS', 10), @('files', 'Remove ATLAS files and shortcuts', 60), @('extra', 'Optional clean-up', 20), @('done', 'Done', 0))
    Ui-Step 'stop' 'Stopping ATLAS'
    if (Stop-Bridge) { Ok 'bridge stopped' }
    Ui-Step 'files' 'Removing files and shortcuts'
    foreach ($f in @((Join-Path $Desktop 'ATLAS.lnk'), (Join-Path $Desktop 'ATLAS.cmd'), (Join-Path $Startup 'ATLAS bridge.lnk'), (Join-Path $Startup 'ATLAS bridge.cmd'))) { if (Test-Path $f) { Remove-Item $f -Force; Ok "removed $f" } }
    if (Test-Path $StartMenu) { Remove-Item $StartMenu -Recurse -Force; Ok 'removed Start Menu entries' }
    if (Test-Path $AppDir) { Remove-Item $AppDir -Recurse -Force -ErrorAction SilentlyContinue; Ok "removed $AppDir" }
    Ui-Step 'extra' 'Optional clean-up'
    if ($o.delete_data -and (Test-Path $Data)) { Remove-Item $Data -Recurse -Force -ErrorAction SilentlyContinue; Ok "removed $Data (your saved data)" }
    elseif (Test-Path $Data) { Say "your saved data stays in $Data" }
    if ($o.hermes_api_off -and (Find-Hermes)) { hermes config set API_SERVER_ENABLED false | Out-Null; hermes gateway restart --detach --yes | Out-Null; Ok 'Hermes API server disabled' }
    Say "Hermes Agent itself was NOT touched. The API key ATLAS added (API_SERVER_KEY in Hermes' .env) stays, so other tools keep working."
    Refresh-IconCache
    Ui-Done $true 'ATLAS was uninstalled' 'Hermes Agent was left alone. You can delete this installer file.'
}

function Run-Install($o, [string]$mode) {
    $isUpdate = [bool]$Update      # only the silent /update skips the optional downloads; an interactive re-run honours the Options page
    $plan = @(@('copy', 'Copy files', 6), @('python', 'Python 3.11+', 8), @('components', 'Install components', 42), @('hermes', 'Hermes Agent (find or install, connect key, start)', 24))
    if ($o.ollama) { $plan += , @('ollama', 'Ollama (local models)', 12) }
    $plan += , @('shortcuts', 'Shortcuts and start-at-login', 6)
    $plan += , @('done', 'Done', 0)
    Ui-Plan $plan

    # ---- 1. copy files ----
    Ui-Step 'copy' 'Copying ATLAS files'
    Migrate-FromOldName
    $mv = Join-Path $AppDir '.venv\Scripts\python.exe'    # a Python environment that moved along with the data folder normally still works; if not it is rebuilt
    if (Test-Path $mv) { & $mv -c 'import aiohttp, psutil' 2>$null; if ($LASTEXITCODE -ne 0) { Remove-Item (Join-Path $AppDir '.venv') -Recurse -Force -ErrorAction SilentlyContinue } }
    if (Stop-Bridge) {
        for ($i = 0; $i -lt 40; $i++) { try { Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$(Get-Port)/api/health" -TimeoutSec 1 | Out-Null; Start-Sleep -Milliseconds 500 } catch { break } }
    }
    New-Item -ItemType Directory -Force -Path $AppDir, (Join-Path $Data 'logs') | Out-Null
    foreach ($d in @('bridge', 'assets', 'mac', 'win', 'installer')) { $t = Join-Path $AppDir $d; if (Test-Path $t) { Remove-Item $t -Recurse -Force } }
    Remove-Item (Join-Path $AppDir 'index.html') -Force -ErrorAction SilentlyContinue
    if ($Stage -and (Test-Path $Stage)) { Copy-Item -Path (Join-Path $Stage '*') -Destination $AppDir -Recurse -Force } else { Extract-Payload $AppDir }
    Get-ChildItem $AppDir -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue
    Ok "files installed in $AppDir"

    # ---- 2. Python ----
    Ui-Step 'python' 'Checking for Python 3.11+'
    $Py = $null
    $VenvPy = Join-Path $AppDir '.venv\Scripts\python.exe'
    if ($isUpdate -and (Test-Path $VenvPy)) { Ok 'using the existing Python environment' }
    else {
        $Py = Find-Python
        if (-not $Py) {
            Warn 'No Python 3.11 or newer found.'
            if (Get-Command winget -ErrorAction SilentlyContinue) {
                Ui-Status 'Installing Python 3.12 with winget (this can take a few minutes)...'
                $rc = Run-Logged 'winget' @('install', '-e', '--id', 'Python.Python.3.12', '--accept-package-agreements', '--accept-source-agreements')
                Refresh-Path; $Py = Find-Python
            } else {
                [void](Open-DefaultBrowser 'https://www.python.org/downloads/windows/')
                Fail-Install 'python' 'Python 3.11+ is needed. python.org was opened: install Python 3.12 (tick "Add python.exe to PATH"), then run this installer again.'
            }
        }
        if (-not $Py) { Fail-Install 'python' 'Python 3.11+ could not be installed. Install it from python.org (tick "Add python.exe to PATH") and run this installer again.' }
        Ok "Python: $Py"
    }

    # ---- 3. components ----
    Ui-Step 'components' 'Setting up the Python environment'
    if (-not (Test-Path $VenvPy)) { & $Py -m venv (Join-Path $AppDir '.venv'); if ($LASTEXITCODE -ne 0) { Fail-Install 'components' 'Could not create the Python environment.' } }
    [void](Run-Logged $VenvPy @('-m', 'pip', 'install', '--quiet', '--upgrade', 'pip'))
    Ui-Status 'Installing ATLAS packages (aiohttp, faster-whisper, piper-tts, psutil...) - a few minutes the first time' 0.1
    $rc = Run-Logged $VenvPy @('-m', 'pip', 'install', '--disable-pip-version-check', '--progress-bar', 'off', '-r', (Join-Path $AppDir 'bridge\requirements.txt'))
    if ($rc -ne 0) { Fail-Install 'components' 'Installing the Python packages failed (see Details). Check your internet connection and run the installer again.' }
    Ok 'Python packages installed'
    Ui-Status 'Checking optional feature packages' 0.55
    $rc = Run-Logged $VenvPy @((Join-Path $AppDir 'bridge\bridge.py'), '--install-deps')
    if ($rc -ne 0) { Warn 'some optional feature packages could not be installed (see Settings in ATLAS)' }
    if ($o.cuda -and -not $isUpdate) {
        Ui-Status 'Installing the NVIDIA CUDA libraries (about 1 GB)...' 0.6
        $rc = Run-Logged $VenvPy @('-m', 'pip', 'install', '--progress-bar', 'off', 'nvidia-cublas-cu12', 'nvidia-cudnn-cu9')
        if ($rc -eq 0) {
            $cfg = Join-Path $Data 'config.json'
            & $VenvPy -c "import json,sys; p=sys.argv[1]; c=json.load(open(p)) if __import__('os').path.exists(p) else {}; c.setdefault('stt',{})['device']='cuda'; json.dump(c,open(p,'w'),indent=2)" $cfg
            Ok 'speech recognition set to use the GPU (stt.device = cuda in config.json)'
        } else { Warn 'the CUDA libraries could not be installed - speech recognition stays on the CPU' }
    }
    if ($o.speech -and -not $isUpdate) {
        Ui-Status 'Downloading the speech models (about 210 MB)...' 0.7
        $rc = Run-Logged $VenvPy @((Join-Path $AppDir 'bridge\bridge.py'), '--prefetch')
        if ($rc -ne 0) { Warn 'model download had problems - ATLAS will retry on first use' }
    }
    if ($o.wake -and -not $isUpdate) {
        Ui-Status 'Downloading the wake-word model (about 55 MB)...' 0.9
        $rc = Run-Logged $VenvPy @((Join-Path $AppDir 'bridge\bridge.py'), '--prefetch-wake')
        if ($rc -ne 0) { Warn 'the wake-word model could not be downloaded now - Settings > Features will fetch it when you switch the wake word on' }
    }

    # ---- 4. Hermes: find / install, connect the key, start ----
    Ui-Step 'hermes' 'Looking for Hermes Agent'
    $HermesOk = Find-Hermes
    if (-not $HermesOk -and $o.hermes -and -not $isUpdate) {
        Ui-Status 'Installing Hermes Agent with its official installer (a few minutes, nothing to type)...' 0.1
        try {
            $hi = Join-Path $env:TEMP 'hermes-install.ps1'
            Invoke-WebRequest -UseBasicParsing -Uri 'https://hermes-agent.nousresearch.com/install.ps1' -OutFile $hi
            $rc = Run-Logged (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $hi, '-NonInteractive')
            Remove-Item $hi -Force -ErrorAction SilentlyContinue
        } catch { Err "Hermes installer failed: $_" }
        $HermesOk = Find-Hermes
    }
    if ($HermesOk) {
        Ok "hermes: $((Get-Command hermes).Source)"
        Ui-Status 'Connecting ATLAS to Hermes (private key, local API, start)...' 0.55
        $rc = Run-Logged $VenvPy @((Join-Path $AppDir 'bridge\bridge.py'), '--connect-hermes')
        if ($rc -ne 0) { Warn "Could not finish connecting to Hermes now - ATLAS's first screen has a 'Fix it' button for that." }
    } else { Warn "Hermes is not installed (yet). That's fine - ATLAS's first screen installs it with one click." }

    # ---- 5. Ollama (optional) ----
    if ($o.ollama -and -not $isUpdate) {
        Ui-Step 'ollama' 'Downloading Ollama (about 1.9 GB)'
        try {
            $oi = Join-Path $env:TEMP 'OllamaSetup.exe'
            Invoke-WebRequest -UseBasicParsing -Uri 'https://ollama.com/download/OllamaSetup.exe' -OutFile $oi
            Ui-Status 'Installing Ollama...' 0.8
            $p = Start-Process -FilePath $oi -ArgumentList '/SILENT' -PassThru -Wait
            Remove-Item $oi -Force -ErrorAction SilentlyContinue
            if (Test-Path (Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe')) { Ok 'Ollama installed (ATLAS will offer your local models in its AI step)' } else { Warn 'Ollama setup finished but ollama.exe was not found - you can install it from ollama.com/download later' }
        } catch { Warn "Ollama could not be installed now: $_ (you can get it from ollama.com/download)" }
    }

    # ---- 6. shortcuts ----
    Ui-Step 'shortcuts' 'Creating shortcuts'
    Repair-LegacyLaunchers
    $okAll = $true
    New-Item -ItemType Directory -Force -Path $StartMenu | Out-Null
    $okAll = (New-Shortcut (Join-Path $StartMenu 'ATLAS.lnk') 'Launch' 'ATLAS - personal command interface for Hermes Agent') -and $okAll
    $okAll = (New-Shortcut (Join-Path $StartMenu 'Stop ATLAS.lnk') 'Stop' 'Stop the ATLAS bridge') -and $okAll
    $dl = Join-Path $Desktop 'ATLAS.lnk'
    if ($o.desktop) { $okAll = (New-Shortcut $dl 'Launch' 'ATLAS - personal command interface for Hermes Agent') -and $okAll }
    elseif (Test-Path $dl) { Remove-Item $dl -Force -ErrorAction SilentlyContinue }
    if ($okAll) { Ok 'shortcuts created (pin ATLAS to the taskbar from the Start Menu if you like)' }
    else {
        Warn 'Windows would not let this installer create shortcuts - made an ATLAS.cmd launcher instead.'
        New-CmdLauncher (Join-Path $StartMenu 'ATLAS.cmd') 'Launch'
        if ($o.desktop) { New-CmdLauncher (Join-Path $Desktop 'ATLAS.cmd') 'Launch' }
    }
    $sl = Join-Path $Startup 'ATLAS bridge.lnk'
    if ($o.login) {
        if (New-Shortcut $sl 'Bridge' 'ATLAS bridge at login') { Ok 'start-at-login enabled' }
        else { New-CmdLauncher (Join-Path $Startup 'ATLAS bridge.cmd') 'Bridge'; Ok 'start-at-login enabled (.cmd)' }
    } else {
        foreach ($f in @($sl, (Join-Path $Startup 'ATLAS bridge.cmd'))) { if (Test-Path $f) { Remove-Item $f -Force } }
    }
    Refresh-IconCache
    # remember the choices (an /update reuses them); a fresh install makes ATLAS open straight into its AI sign-in + "about you" screens
    try { ($o | ConvertTo-Json -Compress) | Set-Content -LiteralPath $OptFile -Encoding ASCII } catch { }
    if ($mode -eq 'install') { try { Set-Content -LiteralPath (Join-Path $Data 'first-run') -Value '1' -Encoding ASCII } catch { } }
    Ui-Step 'done' 'All done'
    if ($isUpdate) {
        Start-Process -FilePath $PsExe -ArgumentList (Get-AtlasLaunchArgs 'Bridge') -WindowStyle Hidden
        Ok 'ATLAS updated - the open ATLAS window reloads by itself'
        Ui-Done $true 'ATLAS was updated' 'Your settings, notes and AI connection were kept.'
    } else {
        Ui-Done $true 'ATLAS is installed' 'Press Finish to open it.'
    }
}

# ================================= main =================================
function Exit-Setup([int]$code) {
    Stop-UiServer
    if ($UiRoot -and (Test-Path $UiRoot)) { Start-Sleep -Milliseconds 400; Remove-Item $UiRoot -Recurse -Force -ErrorAction SilentlyContinue }
    exit $code
}
$Mode = if ($Update) { 'update' } elseif ($Action -eq 'uninstall') { 'uninstall' } else { 'install' }
$Installed = Test-Installed
Write-Host ""
Write-Host "  ATLAS  -  personal command interface for Hermes Agent  (Windows installer)" -ForegroundColor Cyan

$answer = $null
$gui = (-not $Yes)
if ($gui) {
    $UiRoot = Join-Path $env:TEMP ('atlas-setup-' + [guid]::NewGuid().ToString('N'))
    $Stage = Join-Path $UiRoot 'payload'
    Extract-Payload $Stage
    $uiPage = Join-Path $Stage 'installer\ui.html'
    $AtlasVersion = ([regex]::Match((Get-Content (Join-Path $Stage 'bridge\atlas_meta.py') -Raw), 'VERSION\s*=\s*"([^"]+)"')).Groups[1].Value
    $script:UiDir = Join-Path $UiRoot 'ui'; New-Item -ItemType Directory -Force -Path $script:UiDir | Out-Null
    Copy-Item $uiPage (Join-Path $script:UiDir 'ui.html') -Force
    $tok = -join ((48..57) + (97..122) | Get-Random -Count 24 | ForEach-Object { [char]$_ })
    $port = Start-UiServer $script:UiDir $tok
    if (-not $port) {
        if ($script:HiddenChild) { Set-ChildState 'ui-failed'; exit 3 }        # the visible copy carries on in its console
        Write-Host '  (the installer window could not start; using the saved or default choices)'; $script:UiDir = ''; $gui = $false
    }
    else {
        $script:UiState.version = $AtlasVersion; $script:UiState.installed = $Installed; $script:UiState.installed_version = (Get-InstalledVersion)
        $script:UiState.phase = 'starting'; Ui-Flush
        $url = "http://127.0.0.1:$port/?t=$tok"
        if (-not (Open-UiWindow $url (Join-Path $UiRoot 'profile'))) {
            if ($script:HiddenChild) { Set-ChildState 'ui-failed'; exit 3 }    # nobody could see the address: let the visible copy show it
            Write-Host "  Open this address in your browser to continue: $url"
        }
        else { Write-Host "  The ATLAS Setup window is open. (If you do not see it, open $url )"; Set-ChildState 'ui-up' }
    }
}
if ($gui) {
    # welcome (already installed) -> options -> run
    $phase = if ($Installed -and $Action -ne 'uninstall') { 'welcome' } else { 'options' }
    if ($Installed -and $Action -ne 'uninstall') { $Mode = 'update' }
    $script:UiState.mode = $Mode
    while ($true) {
        if ($phase -eq 'welcome') {
            $script:UiState.phase = 'welcome'; Ui-Flush
            $answer = Wait-Answer
            if (-not $answer -or $answer.action -eq 'cancel') { $script:UiState.phase = 'bye'; $script:UiState.status = 'Setup closed - nothing was changed.'; Ui-Flush; Start-Sleep -Milliseconds 900; Exit-Setup 0 }
            if ($answer.action -eq 'choose') { $Mode = $(if ($answer.mode -eq 'uninstall') { 'uninstall' } else { 'update' }); $phase = 'options' }
            continue
        }
        $defs = Get-OptionDefs $Mode
        $script:UiState.mode = $Mode; $script:UiState.options = @($defs | ForEach-Object { $d = $_; [ordered]@{ id = $d.id; group = $d.group; label = $d.label; desc = $d.desc; size = $d.size; value = [bool]$d.value; disabled = [bool]$d.disabled } })
        $script:UiState.can_back = ($Installed -and $Action -ne 'uninstall')
        $script:UiState.blurb = $(if ($Mode -eq 'uninstall') { 'Nothing is deleted until you press Uninstall.' } elseif ($Mode -eq 'update') { 'Your earlier choices are filled in. Nothing is downloaded that you leave unticked.' } else { 'Everything here is optional except what is marked required. Sizes are approximate downloads.' })
        $script:UiState.phase = 'options'; Ui-Flush
        $answer = Wait-Answer
        if (-not $answer -or $answer.action -eq 'cancel') { $script:UiState.phase = 'bye'; $script:UiState.status = 'Setup closed - nothing was changed.'; Ui-Flush; Start-Sleep -Milliseconds 900; Exit-Setup 0 }
        if ($answer.action -eq 'back') { $phase = 'welcome'; continue }
        if ($answer.action -eq 'start') { $opts = Get-OptionValues $defs $answer.options; break }
    }
} else {
    $defs = Get-OptionDefs $Mode
    $opts = if ($Update) { Get-UpdateOptions $defs } else { Get-OptionValues $defs $null }
}
$script:UiState.mode = $Mode
$script:UiState.phase = 'running'
$code = 0
try {
    if ($Mode -eq 'uninstall') { Run-Uninstall $opts } else { Run-Install $opts $Mode }
} catch {
    $code = 1
    $why = if ($script:Failed) { $script:Failed } else { "Unexpected problem: $($_.Exception.Message)" }
    if ($gui) { Ui-Done $false 'Setup could not finish' $why } else { Err $why }
}
if ($gui) {
    $a = Wait-Answer 900
    if ($a -and $a.action -eq 'finish' -and $a.open -and $code -eq 0) { Start-Process -FilePath $PsExe -ArgumentList (Get-AtlasLaunchArgs 'Launch') -WindowStyle Hidden }
    Start-Sleep -Milliseconds 600
}
Exit-Setup $code
