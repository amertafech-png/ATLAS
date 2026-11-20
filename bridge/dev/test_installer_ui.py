"""Dev-only: drives the REAL macOS installer window (ATLAS-Setup.command + perl server + src/installer/ui.html) in headless Chrome on Linux.
   Fresh install (Options page -> step list -> Finish), second run (welcome / update), uninstall. Needs: dist built, mock_hermes (8642).
   /workspace/pwenv/bin/python bridge/dev/test_installer_ui.py [--shots]      (--shots writes docs/installer-*.png)"""
import json, os, re, shutil, subprocess, sys, time, zipfile
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).resolve().parents[2]; DOCS = ROOT / "docs"
SHOTS = "--shots" in sys.argv
SB = Path("/tmp/sb"); HOME = SB / "home"
ENV = dict(os.environ, HERMES_HOME=str(HOME / ".hermes"), HOME=str(HOME), ATLAS_ALLOW_LINUX="1", ATLAS_HOME=str(HOME / "atlas-data"), ATLAS_APPS_DIR=str(HOME / "Applications"),
           ATLAS_LAUNCHAGENTS_DIR=str(HOME / "LA"), PATH="/tmp/sb/bin2:/tmp/fb:/usr/local/bin:/usr/bin:/bin", ATLAS_UI_NO_OPEN="1",
           MODI_HOME=str(HOME / "modi-data"), MODI_APPS_DIR=str(HOME / "Applications"), MODI_LAUNCHAGENTS_DIR=str(HOME / "LA"),   # legacy-modi
           ATLAS_OLLAMA_URL="file:///tmp/sb/Ollama-darwin.zip", ATLAS_HERMES_INSTALL_URL="file:///tmp/sb/hermes-install.sh")
DATA = HOME / "atlas-data"; fails = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (" " + str(extra) if extra and not cond else "")); (None if cond else fails.append(name))
def setup_fixtures():
    for p in (DATA, HOME / "Applications", HOME / "LA", HOME / ".local", HOME / ".hermes", HOME / "modi-data"): shutil.rmtree(p, ignore_errors=True)   # legacy-modi
    (HOME / ".hermes").mkdir(parents=True)
    (SB / "hermes-install.sh").write_text('#!/bin/bash\necho "Installing Hermes Agent (mock)"; mkdir -p "$HOME/.local/bin"; cp /tmp/sb/bin/hermes "$HOME/.local/bin/hermes"; echo done\n')
    with zipfile.ZipFile(SB / "Ollama-darwin.zip", "w") as z:
        z.writestr("Ollama.app/Contents/Resources/ollama", "#!/bin/sh\nexit 0\n"); z.writestr("Ollama.app/Contents/Info.plist", "<plist/>")
def start(*args):
    p = subprocess.Popen(["bash", str(ROOT / "dist/ATLAS-Setup.command"), *args], env=ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=str(SB))
    out = []; t0 = time.time(); url = None
    while time.time() - t0 < 60 and not url:
        l = p.stdout.readline()
        if not l: break
        out.append(l); m = re.search(r"ATLAS_UI_URL=(\S+)", l); url = m.group(1) if m else None
    return p, url, out
def shot(pg, name):
    if SHOTS: pg.screenshot(path=str(DOCS / name)); print("  shot", name)
def phase(pg, want, timeout=900):
    pg.wait_for_function("w => document.body.dataset.phase === w || !!document.querySelector('[data-phase=\"'+w+'\"]')", arg=want, timeout=timeout*1000) if False else None
def wait_text(pg, text, timeout=900):
    pg.wait_for_function("t => document.body.innerText.toLowerCase().includes(t.toLowerCase())", arg=text, timeout=timeout * 1000)
setup_fixtures()
mh = subprocess.Popen(["/workspace/bridge-venv/bin/python", "mock_hermes.py"], cwd=str(ROOT / "bridge/dev"), env=dict(os.environ, MOCK_HERMES_HOME=str(HOME / ".hermes")), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); time.sleep(1.5)
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        ctx = b.new_context(viewport={"width": 920, "height": 900}); pg = ctx.new_page(); errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        # ---- 1. fresh install ----
        p, url, out = start()
        check("installer window server started", bool(url), out[-3:])
        pg.goto(url); wait_text(pg, "Options", 60) if False else pg.wait_for_selector("#go", timeout=60000)
        body = pg.inner_text("body")
        for t in ("Install Hermes Agent", "Install Ollama for local models", "Download the speech models now", "wake-word model", "Start ATLAS when I log in", "~230 MB"):
            check("options page shows: " + t, t in body, body[:200])
        check("no NVIDIA/CUDA row on macOS", "CUDA" not in body)
        check("no Python row when Python exists", "Python 3.12 (required)" not in body)
        shot(pg, "installer-options.png")
        # tick Ollama, wake word ; leave speech as default
        for lab in ("Install Ollama for local models", "wake-word model"):
            pg.locator("label", has_text=lab).first.click()
        pg.wait_for_timeout(300); shot(pg, "installer-options-chosen.png")
        pg.click("#go")
        pg.wait_for_function("document.body.innerText.includes('Install components')", timeout=30000)
        # grab a mid-run screenshot while a step is running and earlier ones are ticked
        pg.wait_for_function("document.querySelectorAll('.st.ok').length >= 2 && !!document.querySelector('.st.run')", timeout=300000)
        pg.wait_for_timeout(700); shot(pg, "installer-running.png")
        check("step list: ticked steps + a highlighted current step + progress bar", pg.locator(".st.ok").count() >= 2 and pg.locator(".st.run").count() == 1 and pg.locator(".bar").count() == 1)
        wait_text(pg, "ATLAS is installed", 1500)
        pg.wait_for_timeout(500); shot(pg, "installer-done.png")
        txt = pg.inner_text("body")
        for t in ("Copy files", "Python", "Install components", "Hermes", "Ollama", "Done"): check("final list has " + t, t in txt)
        check("done page tells where ATLAS opens", "sign" in txt.lower() or "Finish" in txt, txt[-300:])
        pg.click("#fin"); p.wait(timeout=60); out += p.stdout.read().splitlines(True)
        check("installer exit 0", p.returncode == 0, p.returncode)
        check("no console prompt text in output", not re.search(r"\[(Y/n|y/N)\]|Press Return|Choose \[", "".join(out)))
        opts = json.loads((DATA / "install-options.json").read_text())
        check("saved options: ollama+wake on, speech on, login on", opts == {"speech": True, "wake": True, "ollama": True, "login": True, "hermes": True}, opts)
        check("first-run marker written", (DATA / "first-run").exists())
        check("Ollama.app installed", (HOME / "Applications/Ollama.app/Contents/Resources/ollama").exists())
        check("ATLAS.app created", (HOME / "Applications/ATLAS.app/Contents/MacOS/ATLAS").exists())
        check("login item written", (HOME / "LA/com.atlas.bridge.plist").exists())
        check("hermes installed + connected", (HOME / ".local/bin/hermes").exists() and "API_SERVER_KEY" in (HOME / ".hermes/.env").read_text() if (HOME / ".hermes/.env").exists() else False)
        import urllib.request
        up = False
        for _ in range(40):   # Finish -> 'open ATLAS.app' on macOS; on this Linux test run it starts the same launcher script, which brings the bridge up
            try: urllib.request.urlopen("http://127.0.0.1:8765/api/health", timeout=1); up = True; break
            except Exception: time.sleep(1)
        check("Finish launched ATLAS (bridge up)", up)
        if up:
            h = json.load(urllib.request.urlopen("http://127.0.0.1:8765/api/health")); check("bridge reports setup.first_run", (h.get("setup") or {}).get("first_run") is True, h.get("setup"))
        check("no page errors", not errs, errs)
        # ---- 2. second run -> welcome page ----
        subprocess.run(["pkill", "-f", "bridge/bridge.py"]); time.sleep(1)
        p, url, out = start(); pg.goto(url); pg.wait_for_selector("[data-a=update]", timeout=60000); shot(pg, "installer-welcome.png")
        check("welcome offers update + uninstall", pg.locator("[data-a=uninstall]").count() == 1)
        pg.click("[data-a=update]"); pg.wait_for_selector("#go", timeout=30000)
        check("update page: saved choices pre-filled (Ollama already installed -> not offered)", "Install Ollama" not in pg.inner_text("body"))
        pg.click("#back"); pg.wait_for_selector("[data-a=uninstall]", timeout=30000)
        pg.click("[data-a=uninstall]"); pg.wait_for_selector("#go", timeout=30000); shot(pg, "installer-uninstall.png")
        check("uninstall page has data checkbox", "delete" in pg.inner_text("body").lower())
        pg.click("#cancel"); p.wait(timeout=30); check("cancel exits 0, nothing changed", p.returncode == 0 and (DATA / "app/bridge/bridge.py").exists())
        # ---- 3. uninstall ----
        p, url, out = start("--uninstall"); pg.goto(url); pg.wait_for_selector("#go", timeout=60000); pg.click("#go")
        wait_text(pg, "was uninstalled", 120); shot(pg, "installer-uninstalled.png"); pg.click("#fin"); p.wait(timeout=30)
        check("uninstall removed app + bundle + login item, kept data", not (DATA / "app").exists() and not (HOME / "Applications/ATLAS.app").exists() and not (HOME / "LA/com.atlas.bridge.plist").exists() and DATA.exists())
        b.close()
finally:
    mh.terminate(); subprocess.run(["pkill", "-f", "ui-server.pl"])
print("FAILED: %s" % fails if fails else "ALL PASSED"); sys.exit(1 if fails else 0)
