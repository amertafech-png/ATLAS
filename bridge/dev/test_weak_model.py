"""A weak local model (hermes3 8B ...) answers with tool-call text: the bridge must retry once, then show a friendly message (and the HUD must
   offer the AI step), "Test my AI" must catch it, and long answers must not be cut.  Real bridge + mock Hermes (MOCK_ECHO=once|always).
   /workspace/pwenv/bin/python bridge/dev/test_weak_model.py"""
from pathlib import Path
from playwright.sync_api import sync_playwright
HERE = Path(__file__).resolve().parent
src = (HERE / "test_setup_e2e.py").read_text().split("# ---------------------------------------------------------------- 1.")[0]
exec(compile(src, "test_setup_e2e_prefix", "exec"))
PY = "/workspace/bridge-venv/bin/python"; URL = "http://127.0.0.1:%d/" % BR_PORT

def run(mode, fn):
    e = Env("weak-" + mode, shim=False, env_text="API_SERVER_ENABLED=true\nAPI_SERVER_KEY=weak-key-0123456789abcdef\n")
    if mode != "none": e.env["MOCK_ECHO"] = mode
    try:
        e.env["MOCK_HERMES_HOME"] = str(e.hh); e.up(); fn(e)
    finally: e.down()

def once(e):
    c, j = req("POST", "/api/chat", {"text": "what time is it"})
    check("echo once: retried and the user got a real reply", c == 200 and j.get("ok") and "Tool:" not in j["reply"] and "function call" not in j["reply"].lower(), (c, j))
run("once", once)

def always(e):
    c, j = req("POST", "/api/chat", {"text": "what time is it"})
    check("echo always: friendly error, not the raw text", c == 502 and j.get("ok") is False and "Tool:" not in j["error"] and "Qwen" in j["error"], (c, j))
    check("echo always: HUD is told to open the AI step (fix=model)", j.get("fix") == "model", j)
    c, j = req("POST", "/api/setup/test", {})
    r = j.get("result", {})
    check("Test my AI catches the echo", r.get("ok") is False and r.get("kind") == "tool_echo" and "Qwen" in r.get("error", ""), r)
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        pg = b.new_context(viewport={"width": 1600, "height": 900}).new_page(); errs = []; pg.on("pageerror", lambda x: errs.append(str(x)))
        pg.goto(URL); pg.wait_for_function("window.__hud&&__hud.state().online===true", timeout=20000)
        pg.evaluate("__hud.SU.dismissed=true"); pg.fill("#cmdin", "what time is it"); pg.press("#cmdin", "Enter")
        pg.wait_for_function("__hud.state().captionErr", timeout=30000); pg.wait_for_selector("#setup.open", timeout=15000); pg.wait_for_selector(".tile", timeout=10000)
        check("HUD: friendly message shown and AI picker opened", "qwen" in pg.evaluate("__hud.state().caption").lower() and "WHICH AI" in pg.inner_text("#setup").upper(), pg.evaluate("__hud.state().caption"))
        check("no page errors", not errs, errs)
        # Settings -> AI connection -> Test my AI
        pg.evaluate("document.getElementById('setup').classList.remove('open');__hud.SU.open=false")
        pg.evaluate("__hud.ATLAS.openSettings('ai')"); pg.wait_for_selector("#aitest", timeout=10000); pg.click("#aitest")
        pg.wait_for_function("!document.getElementById('aitestmsg').textContent.startsWith('Asking')&&document.getElementById('aitestmsg').textContent.length>5", timeout=30000)
        check("Settings: 'Test my AI' shows the weak-model message", "Qwen" in pg.inner_text("#aitestmsg"), pg.inner_text("#aitestmsg"))
        b.close()
run("always", always)

def good(e):
    c, j = req("POST", "/api/chat", {"text": "give me a longreply please"})
    check("long answers are not cut at 300 characters", c == 200 and len(j["reply"]) > 600, (c, len(j.get("reply", ""))))
    c, j = req("POST", "/api/setup/test", {}); check("Test my AI passes on a healthy model", j["result"].get("ok") is True, j)
run("none", good)
print("FAILED: %s" % fails if fails else "ALL PASSED"); sys.exit(1 if fails else 0)
