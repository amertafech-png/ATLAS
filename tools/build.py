"""Build the MODI release assets into dist/ :
   MODI-Setup.bat (Windows), MODI-Setup.command (macOS), SHA256SUMS, RELEASE-NOTES-v<version>.md (template)
Version + repo come from bridge/modi_meta.py.  See RELEASING.md."""
import base64, hashlib, io, os, re, sys, tarfile, zipfile
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(ROOT, 'bridge'))
import modi_meta
SRC = os.path.join(ROOT, 'src'); DIST = os.path.join(ROOT, 'dist'); os.makedirs(DIST, exist_ok=True)

FILES = {  # archive path -> source path
    'index.html': 'index.html',
    'assets/modi.ico': 'assets/modi.ico', 'assets/modi.png': 'assets/modi.png', 'assets/modi.icns': 'assets/modi.icns',
    'bridge/bridge.py': 'bridge/bridge.py', 'bridge/config.json': 'bridge/config.json',
    'bridge/requirements.txt': 'bridge/requirements.txt',
    'bridge/modi_meta.py': 'bridge/modi_meta.py', 'bridge/modi_update.py': 'bridge/modi_update.py',
    'bridge/modi_features.py': 'bridge/modi_features.py', 'bridge/modules/_example.py': 'bridge/modules/_example.py',
    'bridge/modules/wake_word.py': 'bridge/modules/wake_word.py', 'bridge/modules/wake_word.js': 'bridge/modules/wake_word.js',
    'win/MODI.ps1': 'src/win/MODI.ps1', 'win/MODI.vbs': 'src/win/MODI.vbs',
    'mac/modi-launch.sh': 'src/mac/modi-launch.sh',
    'GUIDE.md': 'docs/GUIDE.md', 'LICENSE': 'LICENSE',
}
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
p = os.path.join(DIST, 'MODI-Setup.command'); open(p, 'w', newline='\n').write(cmd); os.chmod(p, 0o755)

# ---- Windows ----
stub = r'''@echo off
rem MODI-Setup.bat - one-file installer. Double-click. (Windows SmartScreen: More info > Run anyway)
title MODI Setup
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$t=[IO.File]::ReadAllText('%~f0'); $a=$t.IndexOf('#<<'+'PS1>>'); $b=$t.IndexOf('#<<'+'PAYLOAD>>'); $d=Join-Path $env:TEMP 'modi-setup'; New-Item -ItemType Directory -Force -Path $d | Out-Null; $f=Join-Path $d 'MODI-Setup.ps1'; [IO.File]::WriteAllText($f,$t.Substring($a,$b-$a),(New-Object Text.UTF8Encoding($true))); $pf=Join-Path $d 'payload.b64'; [IO.File]::WriteAllText($pf,$t.Substring($b+12)); $env:MODI_PAYLOAD_FILE=$pf; & $f %*; $c=$LASTEXITCODE; Remove-Item $d -Recurse -Force -ErrorAction SilentlyContinue; exit $c"
exit /b %errorlevel%
'''
body = open(os.path.join(SRC, 'win/installer-body.ps1'), 'rb').read().decode().replace('\r\n', '\n')
body.encode('ascii')
bat = stub.replace('\r\n', '\n') + body.rstrip('\n') + '\n#<<PAYLOAD>>\n' + b64lines(make_zip())
bat = bat.replace('\n', '\r\n')
open(os.path.join(DIST, 'MODI-Setup.bat'), 'w', newline='').write(bat)

# ---- checksums + release notes template ----
ASSETS = ['MODI-Setup.bat', 'MODI-Setup.command']
for old in ('MODI-source.zip',):
    if os.path.exists(os.path.join(DIST, old)): os.remove(os.path.join(DIST, old))
sums = ''
for a in ASSETS:
    h = hashlib.sha256(open(os.path.join(DIST, a), 'rb').read()).hexdigest()
    sums += f'{h}  {a}\n'
open(os.path.join(DIST, modi_meta.CHECKSUMS_ASSET), 'w', newline='\n').write(sums)
V, R = modi_meta.VERSION, modi_meta.REPO
_hl = os.path.join(ROOT, 'docs', 'RELEASE-HIGHLIGHTS.md')   # optional: the "What's new" bullets for this version
highlights = open(_hl, encoding='utf-8').read().strip() if os.path.exists(_hl) else '- (write the highlights here)'
notes = f"""## MODI v{V}

### What's new
{highlights}

### Install
- **Windows 10/11:** download `MODI-Setup.bat` and double-click it. If SmartScreen says "Windows protected your PC": **More info -> Run anyway**.
- **macOS 11+:** download `MODI-Setup.command`, then right-click it -> **Open** -> **Open** (or run `xattr -d com.apple.quarantine ~/Downloads/MODI-Setup.command`).
- Already have MODI? Open it - an **Update** button appears in the top bar (or run the new installer again; your settings are kept).
- You need your own AI account: the installer sets up Hermes Agent and asks you to sign in. See the README.

### Checksums (SHA-256)
MODI's updater refuses to install unless these match the downloaded files. They are also in the `{modi_meta.CHECKSUMS_ASSET}` asset.
```
{sums.rstrip()}
```
"""
open(os.path.join(DIST, f'RELEASE-NOTES-v{V}.md'), 'w', newline='\n').write(notes)
print(f'MODI v{V}  repo {R}  tag must be v{V}')
for f in sorted(os.listdir(DIST)): print(' ', f, os.path.getsize(os.path.join(DIST, f)))
