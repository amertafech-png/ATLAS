"""Dev-only (v1.4.2): the Windows app-window browser choice and switches, run under pwsh with the REAL functions from src/win/ATLAS.ps1
(and the installer's copies), with the browser lookup stubbed.  Everything happens in a temp folder.
  * the default browser wins if it is Chrome/Edge/Brave; otherwise Chrome before Edge before Brave
  * Edge gets --disable-features=msImplicitSignin,... (one --disable-features), every browser gets a private profile + --disable-sync
  * a new profile is pre-seeded with sign-in switched off and keeps the HUD's saved notes from the old profile
   python3 bridge/dev/test_app_window.py"""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
PWSH = os.environ.get("PWSH") or shutil.which("pwsh") or "/home/box/pwsh/pwsh"
fails = []
def check(n, ok, info=""):
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else " :: %s" % (info,))); ok or fails.append(n)
if not os.path.exists(PWSH):
    print("SKIP: pwsh not found"); sys.exit(0)
tmp = Path(tempfile.mkdtemp(prefix="atlas-appwin-"))
try:
    for script in ("src/win/ATLAS.ps1", "src/win/installer-body.ps1"):
        ps = r'''
$ErrorActionPreference = 'Stop'
$ast = [System.Management.Automation.Language.Parser]::ParseFile('%s', [ref]$null, [ref]$null)
$want = @('Get-DefaultBrowserKind','Find-BrowserExe','Find-Browser','Get-AppWindowArgs','Initialize-AppProfile','Get-FullscreenPref')
foreach ($f in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)) {
    if ($want -contains $f.Name) { . ([scriptblock]::Create($f.Extent.Text)) }
}
$out = @{}
$script:Def = ''; $script:Have = @()
function Get-DefaultBrowserKind { return $script:Def }
function Find-BrowserExe([string]$kind) { if ($script:Have -contains $kind) { return "C:\fake\$kind.exe" } return $null }
foreach ($case in @(@('', 'chrome,msedge'), @('msedge', 'chrome,msedge'), @('', 'msedge'), @('brave', 'chrome,msedge,brave'), @('', 'brave'), @('chrome', ''))) {
    $script:Def = $case[0]; $script:Have = @($case[1] -split ',' | Where-Object { $_ })
    $b = Find-Browser
    $out["pick:" + $case[0] + ":" + $case[1]] = $(if ($b) { $b.Kind } else { '' })
}
$out['edge'] = (Get-AppWindowArgs 'msedge' 'http://127.0.0.1:8765' 'C:\p' '1600,900')
$out['chrome'] = (Get-AppWindowArgs 'chrome' 'http://127.0.0.1:8765' 'C:\p' '1600,900')
$out['kiosk_off'] = (Get-AppWindowArgs 'chrome' 'http://127.0.0.1:8765' 'C:\p' '1600,900' $false)
$out['kiosk_on'] = (Get-AppWindowArgs 'chrome' 'http://127.0.0.1:8765' 'C:\p' '1600,900' $true)
$out['kiosk_edge'] = (Get-AppWindowArgs 'msedge' 'http://127.0.0.1:8765' 'C:\p' '1600,900' $true)
if (Get-Command Get-FullscreenPref -ErrorAction SilentlyContinue) {
    Remove-Item Env:ATLAS_FULLSCREEN -ErrorAction SilentlyContinue
    $Data = '%s'; $Root = '%s'
    $out['fs_default'] = [bool](Get-FullscreenPref)
    $env:ATLAS_FULLSCREEN = '0'; $out['fs_env0'] = [bool](Get-FullscreenPref)
    Remove-Item Env:ATLAS_FULLSCREEN
    Set-Content -LiteralPath (Join-Path $Data 'config.json') '{"prefs":{"fullscreen":false}}'
    $out['fs_file_off'] = [bool](Get-FullscreenPref)
    $env:ATLAS_FULLSCREEN = '1'; $out['fs_env1'] = [bool](Get-FullscreenPref)
    Remove-Item Env:ATLAS_FULLSCREEN
}
if (Get-Command Initialize-AppProfile -ErrorAction SilentlyContinue) {
    $old = Join-Path '%s' 'appdata'; $new = Join-Path '%s' 'browser-profile'
    New-Item -ItemType Directory -Force -Path (Join-Path $old 'Default\Local Storage\leveldb') | Out-Null
    Set-Content (Join-Path $old 'Default\Local Storage\leveldb\000003.log') 'notes'
    Initialize-AppProfile $new $old
    $out['prefs'] = Get-Content -Raw (Join-Path $new 'Default\Preferences')
    $out['copied'] = Test-Path (Join-Path $new 'Default\Local Storage\leveldb\000003.log')
    Set-Content (Join-Path $new 'Default\Preferences') '{"kept":1}'
    Initialize-AppProfile $new $old
    $out['second'] = Get-Content -Raw (Join-Path $new 'Default\Preferences')
}
$out | ConvertTo-Json -Depth 4
''' % (str(ROOT / script), str(tmp).replace("\\", "/"), str(tmp).replace("\\", "/"), str(tmp).replace("\\", "/"), str(tmp).replace("\\", "/"))
        r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True, timeout=120)
        tag = Path(script).name
        try: o = json.loads(r.stdout)
        except Exception: check(tag + ": pwsh ran", False, (r.stdout[-500:], r.stderr[-800:])); continue
        check(tag + ": Firefox/unknown default -> Chrome before Edge", o["pick::chrome,msedge"] == "chrome", o)
        check(tag + ": default Edge is respected", o["pick:msedge:chrome,msedge"] == "msedge", o)
        check(tag + ": only Edge installed -> Edge", o["pick::msedge"] == "msedge", o)
        check(tag + ": default Brave is respected", o["pick:brave:chrome,msedge,brave"] == "brave", o)
        check(tag + ": only Brave installed -> Brave", o["pick::brave"] == "brave", o)
        check(tag + ": no Chromium browser -> none (falls back to the default browser tab)", o["pick:chrome:"] == "", o)
        e, c = o["edge"], o["chrome"]
        for sw in ("--user-data-dir=\"C:\\p\"", "--no-first-run", "--no-default-browser-check", "--disable-sync", "--disable-background-mode", "--app=http://127.0.0.1:8765"):
            check(tag + ": Edge + Chrome get " + sw, sw in e and sw in c, (e, c))
        df = [x for x in e if x.startswith("--disable-features=")]
        check(tag + ": Edge: one --disable-features with msImplicitSignin", len(df) == 1 and "msImplicitSignin" in df[0] and "msEdgeImplicitSignin" in df[0], df)
        check(tag + ": Chrome gets no Edge-only features", not any("msImplicit" in x for x in c), c)
        check(tag + ": a normal app window is not --kiosk", "--kiosk" not in e and "--kiosk" not in c and "--kiosk" not in o["kiosk_off"])
        kon, ke = o["kiosk_on"], o["kiosk_edge"]
        check(tag + ": full screen adds --kiosk and keeps --app (links are not opened here)", "--kiosk" in kon and any(x.startswith("--app=http://127.0.0.1:8765") for x in kon), kon)
        kdf = [x for x in ke if x.startswith("--disable-features=")]
        check(tag + ": kiosk Edge still has one --disable-features", "--kiosk" in ke and len(kdf) == 1 and "msImplicitSignin" in kdf[0], ke)
        if "fs_default" in o:
            check(tag + ": full screen defaults on", o["fs_default"] is True, o.get("fs_default"))
            check(tag + ": ATLAS_FULLSCREEN=0 turns it off", o["fs_env0"] is False, o.get("fs_env0"))
            check(tag + ": config prefs.fullscreen false is remembered", o["fs_file_off"] is False, o.get("fs_file_off"))
            check(tag + ": ATLAS_FULLSCREEN=1 overrides the file", o["fs_env1"] is True, o.get("fs_env1"))
        if "prefs" in o:
            p = json.loads(o["prefs"])
            check(tag + ": new profile starts with browser sign-in off and no sync", p["signin"]["allowed"] is False and p["sync"]["requested"] is False, p)
            check(tag + ": HUD notes/settings copied from the old profile", o["copied"] is True)
            check(tag + ": an existing profile is left alone", json.loads(o["second"]) == {"kept": 1}, o["second"])
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("FAILED: %s" % fails if fails else "ALL PASSED"); sys.exit(1 if fails else 0)
