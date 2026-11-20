"""Dev-only: screenshots of the first-run setup wizard + Settings -> AI connection (headless Chrome vs a real bridge, mock Hermes, mock Ollama).
   /workspace/pwenv/bin/python bridge/dev/shot_setup.py   -> docs/setup-*.png and docs/screenshot.png"""
import os, subprocess, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright
HERE = Path(__file__).resolve().parent
DOCS = HERE.parent.parent / "docs"
src = (HERE / "test_setup_e2e.py").read_text().split("# ---------------------------------------------------------------- 1.")[0]
exec(compile(src, "test_setup_e2e_prefix", "exec"))      # reuses Env / req / wait helpers (starts nothing by itself)
PY = "/workspace/bridge-venv/bin/python"
URL = "http://127.0.0.1:%d/" % BR_PORT
ollama = subprocess.Popen([PY, str(HERE / "mock_ollama.py")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1.5)
try: os.remove("/tmp/mock_hermes_signed_in")
except OSError: pass

def page(b):
    ctx = b.new_context(viewport={"width": 1600, "height": 900}); pg = ctx.new_page()
    errs = []; pg.on("pageerror", lambda e: errs.append(str(e))); return pg, errs

with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    # 1. Hermes not installed -> one-click install card
    e = Env("shot-inst", shim=False, mock_up=False)
    try:
        e.up(); pg, errs = page(b); pg.goto(URL)
        pg.wait_for_selector("#setup.open", timeout=15000); pg.wait_for_selector("[data-su=install]", timeout=15000); pg.wait_for_timeout(600)
        pg.screenshot(path=str(DOCS / "setup-1-install-hermes.png")); print("1 install card", errs)
    finally: e.down()
    # 2. Hermes installed, no key -> "Connect Hermes / Fix it" -> choose AI -> key -> connected
    e = Env("shot-fix", no_provider=True, env_text="OPENROUTER_API_KEY_UNUSED=1\n")
    held = []
    try:
        e.env["MOCK_HERMES_HOME"] = str(e.hh); e.up()
        pg, errs = page(b)
        hold = {"on": True}
        pg.route("**/api/setup/fix", lambda r: held.append(r) if hold["on"] else r.continue_())
        pg.goto(URL); pg.wait_for_selector("#setup.open", timeout=15000); pg.wait_for_selector("[data-su=fix]", timeout=15000); pg.wait_for_timeout(500)
        pg.screenshot(path=str(DOCS / "setup-2-connect-hermes.png")); print("2 fix card", errs)
        hold["on"] = False
        if held: held[0].continue_()
        else: pg.click("[data-su=fix]")
        pg.wait_for_selector("[data-prov]", timeout=60000); pg.wait_for_timeout(500)
        pg.screenshot(path=str(DOCS / "setup-3-choose-ai.png")); print("3 provider picker")
        pg.click("[data-prov=ollama]"); pg.wait_for_timeout(1500)
        pg.screenshot(path=str(DOCS / "setup-4-local-model.png")); print("4 local model")
        pg.click("[data-prov=openrouter]"); pg.wait_for_selector("#sukey", timeout=5000); pg.fill("#sukey", "sk" + "-or-DEMO-not-a-real-key-0000000000"); pg.wait_for_timeout(300)
        pg.screenshot(path=str(DOCS / "setup-5-api-key.png")); print("5 api key form")
        Path("/tmp/mock_hermes_signed_in").write_text("1")
        pg.click("[data-su=connect]"); pg.wait_for_selector("[data-su=done]", timeout=90000); pg.wait_for_timeout(600)
        pg.screenshot(path=str(DOCS / "setup-6-connected.png")); print("6 connected", errs)
        pg.click("[data-su=done]"); pg.wait_for_timeout(1500)
        pg.evaluate("window.__hud.ATLAS.openSettings('ai')"); pg.wait_for_timeout(1500)
        pg.screenshot(path=str(DOCS / "setup-7-settings-ai.png")); print("7 settings tab")
        pg.keyboard.press("Escape"); pg.wait_for_timeout(500)
        # main HUD with a short conversation
        pg.goto(URL); pg.wait_for_timeout(3000)
        pg.fill("#cmdin", "Hey ATLAS, what's the plan for today?"); pg.press("#cmdin", "Enter")
        pg.wait_for_function("document.getElementById('caption').textContent.includes('Mock')", timeout=30000); pg.wait_for_timeout(2500)
        pg.screenshot(path=str(DOCS / "screenshot.png")); print("8 main HUD", errs)
    finally:
        e.down(); b.close()
ollama.terminate()
try: os.remove("/tmp/mock_hermes_signed_in")
except OSError: pass
