"""Build the ATLAS release assets into dist/ :
   ATLAS-Setup.bat (Windows), ATLAS-Setup.command (macOS), SHA256SUMS, RELEASE-NOTES-v<version>.md (template)
Version + repo come from bridge/atlas_meta.py.  See RELEASING.md."""
import base64, hashlib, io, json, os, re, shutil, sys, tarfile, zipfile
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(ROOT, 'bridge'))
import atlas_meta
SRC = os.path.join(ROOT, 'src'); DIST = os.path.join(ROOT, 'dist'); os.makedirs(DIST, exist_ok=True)

FILES = {  # archive path -> source path
    'index.html': 'index.html',
    'assets/atlas.ico': 'assets/atlas.ico', 'assets/atlas.png': 'assets/atlas.png', 'assets/atlas-192.png': 'assets/atlas-192.png', 'assets/atlas-512.png': 'assets/atlas-512.png', 'assets/atlas.icns': 'assets/atlas.icns',
    'bridge/bridge.py': 'bridge/bridge.py', 'bridge/config.json': 'bridge/config.json',
    'bridge/requirements.txt': 'bridge/requirements.txt',
    'bridge/atlas_meta.py': 'bridge/atlas_meta.py', 'bridge/atlas_update.py': 'bridge/atlas_update.py',
    'bridge/atlas_features.py': 'bridge/atlas_features.py', 'bridge/atlas_settings.py': 'bridge/atlas_settings.py', 'bridge/atlas_hermes.py': 'bridge/atlas_hermes.py',
    'bridge/atlas_layout.py': 'bridge/atlas_layout.py', 'bridge/atlas_calendar.py': 'bridge/atlas_calendar.py', 'bridge/atlas_scores.py': 'bridge/atlas_scores.py', 'bridge/atlas_info.py': 'bridge/atlas_info.py', 'bridge/atlas_reminders.py': 'bridge/atlas_reminders.py', 'bridge/atlas_launch.py': 'bridge/atlas_launch.py', 'bridge/atlas_browser.py': 'bridge/atlas_browser.py', 'bridge/modules/computer_control.py': 'bridge/modules/computer_control.py', 'bridge/atlas_modules.py': 'bridge/atlas_modules.py', 'bridge/modules/MANIFEST.json': 'bridge/modules/MANIFEST.json', 'bridge/modules/_example.py': 'bridge/modules/_example.py',
    'bridge/modules/wake_word.py': 'bridge/modules/wake_word.py', 'bridge/modules/wake_word.js': 'bridge/modules/wake_word.js',
    'win/ATLAS.ps1': 'src/win/ATLAS.ps1', 
    'mac/atlas-launch.sh': 'src/mac/atlas-launch.sh', 'mac/ui-server.pl': 'src/mac/ui-server.pl',
    'installer/ui.html': 'src/installer/ui.html',
    'GUIDE.md': 'docs/GUIDE.md', 'LICENSE': 'LICENSE',
}
# Add-on modules that ship with ATLAS are trusted only while their files match bridge/modules/MANIFEST.json (see atlas_modules.py): regenerate it, and make sure every shipped module is packaged.
sys.path.insert(0, os.path.join(ROOT, 'bridge'))
import atlas_modules
_mdir = os.path.join(ROOT, 'bridge', 'modules')
_man = {}
for _f in sorted(os.listdir(_mdir)):
    if _f.endswith('.py') and not _f.startswith('_'):
        _js = os.path.join(_mdir, _f[:-3] + '.js')
        _man[_f[:-3]] = atlas_modules.sha_of(open(os.path.join(_mdir, _f), 'rb').read(), open(_js, 'rb').read() if os.path.exists(_js) else None)
    if (_f.endswith('.py') or _f.endswith('.js')) and not _f.startswith('_') and f'bridge/modules/{_f}' not in FILES:
        sys.exit('build: bridge/modules/' + _f + ' is not listed in FILES (it would be missing from the installers)')
_mtxt = json.dumps({'note': 'SHA-256 of each add-on module that ships with ATLAS (python + optional .js). Written by tools/build.py; a module whose files differ from this needs the user\'s approval before it runs.', 'modules': _man}, indent=1) + '\n'
if not os.path.exists(os.path.join(_mdir, 'MANIFEST.json')) or open(os.path.join(_mdir, 'MANIFEST.json'), encoding='utf-8').read() != _mtxt:
    open(os.path.join(_mdir, 'MANIFEST.json'), 'w', encoding='utf-8', newline='\n').write(_mtxt)
    print('module manifest updated:', ', '.join(_man))
_missing = [f for f in sorted(os.listdir(os.path.join(ROOT, 'bridge'))) if f.endswith('.py') and f'bridge/{f}' not in FILES]
if _missing: sys.exit('build: bridge modules not listed in FILES (they would be missing from the installers): ' + ', '.join(_missing))
# The Windows shortcut icon is assets/atlas.ico: it must be a valid multi-size .ico (16/24/32/48/64/256), or shortcuts show a blank page.
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import icolib
_ok, _msg = icolib.check_ico(os.path.join(ROOT, 'assets', 'atlas.ico'))
if not _ok: sys.exit('build: assets/atlas.ico is not a valid multi-size icon: ' + _msg + '  (run tools/make_icons.py)')
print('icon check: atlas.ico', _msg)
# No console prompts in either installer: every question lives on the Options page of the installer window.
import re as _re
for _f, _pat in (('win/installer-body.ps1', r'Read-Host|ReadKey|\bPause\b|RawUI\.ReadKey|\[Console\]::Read'), ('mac/installer-head.sh', r'(^|[;&|{(]\s*)read\s+(-[a-z]+\s+)*[A-Za-z_]+\s*(2>|<\s*/dev/tty|$)')):
    for _n, _l in enumerate(open(os.path.join(SRC, _f), encoding='utf-8').read().splitlines(), 1):
        if _re.search(_pat, _l) and 'IFS= read -r' not in _l and 'IFS=' not in _l and 'prompt-ok' not in _l:
            sys.exit(f'build: console prompt in src/{_f}:{_n}: {_l.strip()[:90]} (put the question on the Options page instead)')
_body = open(os.path.join(SRC, 'win', 'installer-body.ps1'), encoding='utf-8').read()
if re.search(r"atlas\.ico'\)?\s*\+\s*','", _body): sys.exit("build: the shortcut icon path must not have ',0' appended (use the icon index argument)")
def data(arc, src):
    b = open(os.path.join(ROOT, src), 'rb').read()
    if arc.startswith('win/'):
        b = b.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
        b.decode('ascii')  # Windows scripts must stay ASCII
    elif arc.endswith(('.sh', '.py', '.json', '.txt', '.html', '.md')) or arc == 'LICENSE':
        b = b.replace(b'\r\n', b'\n')
    return b

def make_zip():
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, 'w', zipfile.ZIP_DEFLATED) as z:
        for arc, src in FILES.items(): z.writestr(arc, data(arc, src))
    return bio.getvalue()
def make_tgz():
    bio = io.BytesIO()
    with tarfile.open(fileobj=bio, mode='w:gz') as t:
        for arc, src in FILES.items():
            b = data(arc, src); ti = tarfile.TarInfo(arc); ti.size = len(b); ti.mode = 0o755 if arc.endswith('.sh') else 0o644
            ti.mtime = 1700000000; ti.uid = ti.gid = 0; ti.uname = ti.gname = ''
            t.addfile(ti, io.BytesIO(b))
    return bio.getvalue()
def b64lines(b, n=76):
    s = base64.b64encode(b).decode()
    return '\n'.join(s[i:i+n] for i in range(0, len(s), n)) + '\n'

# ---- macOS ----
head = open(os.path.join(SRC, 'mac/installer-head.sh'), 'rb').read().decode().replace('\r\n', '\n')
cmd = head + b64lines(make_tgz())
p = os.path.join(DIST, 'ATLAS-Setup.command'); open(p, 'w', newline='\n').write(cmd); os.chmod(p, 0o755)

# ---- Windows ----
stub = r'''@echo off
rem ATLAS-Setup.bat - one-file installer. Double-click. (Windows SmartScreen: More info > Run anyway)
title ATLAS Setup
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$t=[IO.File]::ReadAllText('%~f0'); $a=$t.IndexOf('#<<'+'PS1>>'); $b=$t.IndexOf('#<<'+'PAYLOAD>>'); $d=Join-Path $env:TEMP 'atlas-setup'; New-Item -ItemType Directory -Force -Path $d | Out-Null; $f=Join-Path $d 'ATLAS-Setup.ps1'; [IO.File]::WriteAllText($f,$t.Substring($a,$b-$a),(New-Object Text.UTF8Encoding($true))); $pf=Join-Path $d 'payload.b64'; [IO.File]::WriteAllText($pf,$t.Substring($b+12)); $env:ATLAS_PAYLOAD_FILE=$pf; & $f %*; $c=$LASTEXITCODE; Remove-Item $d -Recurse -Force -ErrorAction SilentlyContinue; exit $c"
exit /b %errorlevel%
'''
body = open(os.path.join(SRC, 'win/installer-body.ps1'), 'rb').read().decode().replace('\r\n', '\n')
body.encode('ascii')
bat = stub.replace('\r\n', '\n') + body.rstrip('\n') + '\n#<<PAYLOAD>>\n' + b64lines(make_zip())
bat = bat.replace('\n', '\r\n')
open(os.path.join(DIST, 'ATLAS-Setup.bat'), 'w', newline='').write(bat)

# ---- checksums + release notes template ----
ASSETS = ['ATLAS-Setup.bat', 'ATLAS-Setup.command']
# The v1.1.x updater (still installed on early users' PCs) fetches MODI-Setup.* from this repo: attach byte-identical copies under the old names.   # legacy-modi
for new, old in zip(ASSETS, (atlas_meta.LEGACY_ASSETS['windows'], atlas_meta.LEGACY_ASSETS['mac'])):   # legacy-modi
    shutil.copyfile(os.path.join(DIST, new), os.path.join(DIST, old))
ASSETS += [atlas_meta.LEGACY_ASSETS['windows'], atlas_meta.LEGACY_ASSETS['mac']]   # legacy-modi
for old in ('ATLAS-source.zip',):
    if os.path.exists(os.path.join(DIST, old)): os.remove(os.path.join(DIST, old))
sums = ''
for a in ASSETS:
    h = hashlib.sha256(open(os.path.join(DIST, a), 'rb').read()).hexdigest()
    sums += f'{h}  {a}\n'
open(os.path.join(DIST, atlas_meta.CHECKSUMS_ASSET), 'w', newline='\n').write(sums)
V, R = atlas_meta.VERSION, atlas_meta.REPO
_hl = os.path.join(ROOT, 'docs', 'RELEASE-HIGHLIGHTS.md')   # optional: the "What's new" bullets for this version
highlights = open(_hl, encoding='utf-8').read().strip() if os.path.exists(_hl) else '- (write the highlights here)'
notes = f"""## ATLAS v{V}

### What's new
{highlights}

### Install
- **Windows 10/11:** download `ATLAS-Setup.bat` and double-click it. If SmartScreen says "Windows protected your PC": **More info -> Run anyway**.
- **macOS 11+:** download `ATLAS-Setup.command`, then right-click it -> **Open** -> **Open** (or run `xattr -d com.apple.quarantine ~/Downloads/ATLAS-Setup.command`).
- Already have ATLAS? Open it - an **Update** button appears in the top bar (or run the new installer again; your settings are kept).
- `MODI-Setup.bat` / `MODI-Setup.command` are identical copies under ATLAS's old name. They exist only so older installs (v1.1.x, before the rename) can still update themselves. You can ignore them.   <!-- legacy-modi -->
- You need your own AI account: the installer sets up Hermes Agent and asks you to sign in. See the README.

### Checksums (SHA-256)
ATLAS's updater refuses to install unless these match the downloaded files. They are also in the `{atlas_meta.CHECKSUMS_ASSET}` asset.
```
{sums.rstrip()}
```
"""
open(os.path.join(DIST, f'RELEASE-NOTES-v{V}.md'), 'w', newline='\n').write(notes)
print(f'ATLAS v{V}  repo {R}  tag must be v{V}')
for f in sorted(os.listdir(DIST)): print(' ', f, os.path.getsize(os.path.join(DIST, f)))
