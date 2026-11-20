"""Dev-only: the in-app first-run flow after the installer's Finish button (marker <data>/first-run written by a fresh install):
   ATLAS opens straight into the AI sign-in (ChatGPT/Claude/Grok/OpenRouter/Local), then 'About you' (name/city/sports); the marker is cleared.
   /workspace/pwenv/bin/python bridge/dev/test_firstrun_ui.py [--shots]   -> docs/installer-first-run-*.png"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
HERE = Path(__file__).resolve().parent; DOCS = HERE.parent.parent / "docs"; SHOTS = "--shots" in sys.argv
src = (HERE / "test_setup_e2e.py").read_text().split("# ---------------------------------------------------------------- 1.")[0]
exec(compile(src, "test_setup_e2e_prefix", "exec"))
PY = "/workspace/bridge-venv/bin/python"
URL = "http://127.0.0.1:%d/" % BR_PORT
e = Env("firstrun", env_text="OPENROUTER_API_KEY=placeholder-not-a-secret\n")
(e.data / "first-run").write_text("1")
try:
    e.env["MOCK_HERMES_HOME"] = str(e.hh); e.up()
    check("health reports setup.first_run", req("GET", "/api/health")[1].get("setup", {}).get("first_run") is True)
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        pg = b.new_context(viewport={"width": 1600, "height": 900}).new_page(); errs = []; pg.on("pageerror", lambda x: errs.append(str(x)))
        pg.goto(URL); pg.wait_for_selector("#setup.open", timeout=20000); pg.wait_for_selector(".tile", timeout=20000); pg.wait_for_timeout(600)
        t = pg.inner_text("#setup")
        for n in ("ChatGPT", "Claude", "Grok", "OpenRouter"): check("AI step lists " + n, n.lower() in t.lower())
        check("AI step lists a local option", "local" in t.lower() or "ollama" in t.lower())
        check("first screen is the AI step", "WHICH AI" in t.upper())
        if SHOTS: pg.screenshot(path=str(DOCS / "installer-first-run-ai.png"))
        pg.click("[data-su=back]"); pg.wait_for_selector("[data-su=done]", timeout=10000)
        check("ready step offers 'Next: a bit about you'", "a bit about you" in pg.inner_text("#setup").lower())
        pg.click("[data-su=done]"); pg.wait_for_selector("#abname", timeout=10000)
        pg.fill("#abname", "Adam"); pg.fill("#abcity", "Austin, Texas"); pg.wait_for_timeout(500)
        check("about step has name/city/sports", pg.locator("#abcity").count() == 1 and "sports" in pg.inner_text("#setup").lower())
        pg.click("summary:has-text('Favorite sports')"); pg.wait_for_timeout(400)
        if SHOTS: pg.screenshot(path=str(DOCS / "installer-first-run-about.png"))
        pg.click("[data-su=aboutsave]"); pg.wait_for_function("!document.querySelector('#setup.open')", timeout=10000)
        check("profile saved", req("GET", "/api/profile")[1].get("profile", {}).get("name") == "Adam", req("GET", "/api/profile"))
        check("first-run marker cleared", not (e.data / "first-run").exists() and req("GET", "/api/health")[1]["setup"]["first_run"] is False)
        check("no page errors", not errs, errs)
        pg.reload(); pg.wait_for_timeout(2500); check("does not reopen after a reload", pg.locator("#setup.open").count() == 0)
        b.close()
finally: e.down()
print("FAILED: %s" % fails if fails else "ALL PASSED"); sys.exit(1 if fails else 0)
