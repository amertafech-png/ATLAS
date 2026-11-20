"""Dev-only (v1.4.1):
  * links (sign-in pages, "Get a key", downloads) open in the user's DEFAULT browser via the bridge (POST /api/open-url), not inside the
    Edge/Chrome app window the HUD runs in; only http(s) is accepted and foreign web pages can't trigger it;
  * the HUD has real icons for the app window / taskbar: favicon .ico, png 192/256/512 and a web-app manifest.
The bridge runs with ATLAS_OPEN_URL_LOG so links are recorded, never opened.     /workspace/pwenv/bin/python bridge/dev/test_default_browser_icon.py"""
import json, os, struct, sys, tempfile, urllib.request, urllib.error
from pathlib import Path
from playwright.sync_api import sync_playwright
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import atlas_browser as AB  # noqa: E402
src = (HERE / "test_setup_e2e.py").read_text().split("# ---------------------------------------------------------------- 1.")[0]
exec(compile(src, "test_setup_e2e_prefix", "exec"))
PY = "/workspace/bridge-venv/bin/python"; URL = "http://127.0.0.1:%d/" % BR_PORT

for u in ("https://openrouter.ai/keys", "http://example.com/a?b=c#d", "https://auth.openai.com/oauth/authorize?client_id=x&redirect_uri=http%3A%2F%2Flocalhost%3A1455"):
    check("valid link accepted: " + u[:40], AB.valid(u))
for u in ("file:///C:/Windows/System32/calc.exe", "javascript:alert(1)", "ms-settings:privacy", "https://", "https://a b.com", "https://x.com/\n", "\\\\server\\share", "", None, "https://e.com/" + "a" * 3000, "steam://run/1"):
    check("refused: %r" % (u if not isinstance(u, str) else u[:40]), not AB.valid(u))
check("open_default refuses a bad link without opening anything", AB.open_default("file:///etc/passwd")[0] is False)

def hreq(path, body=None, headers=None):
    r = urllib.request.Request(URL.rstrip("/") + path, data=json.dumps(body).encode() if body is not None else None,
                               headers=dict({"Content-Type": "application/json"}, **(headers or {})))
    try:
        with urllib.request.urlopen(r, timeout=10) as f: return f.status, f.read(), f.headers
    except urllib.error.HTTPError as e: return e.code, e.read(), e.headers

log = Path(tempfile.mkdtemp()) / "opened.txt"
e = Env("defbrowser")
e.env["ATLAS_OPEN_URL_LOG"] = str(log)
try:
    e.up()
    st, b, _ = hreq("/api/open-url", {"url": "https://openrouter.ai/keys"})
    check("POST /api/open-url opens an https link", st == 200 and json.loads(b)["ok"], b)
    st, b, _ = hreq("/api/open-url", {"url": "file:///C:/Windows/System32/cmd.exe"})
    check("POST /api/open-url refuses file: links", st == 400, b)
    st, b, _ = hreq("/api/open-url", {"url": "https://evil.example/"}, {"Origin": "https://evil.example"})
    check("a foreign web page can't make ATLAS open links (403)", st == 403, st)
    # icons + manifest
    st, b, h = hreq("/manifest.webmanifest")
    m = json.loads(b) if st == 200 else {}
    check("manifest served with the ATLAS name and 192/512 icons", st == 200 and m.get("name") == "ATLAS" and {"192x192", "512x512"} <= {i["sizes"] for i in m.get("icons", [])}, (st, m))
    for path, size in (("/atlas-192.png", 192), ("/atlas-512.png", 512), ("/atlas.png", 256)):
        st, b, h = hreq(path)
        w, hh = struct.unpack(">II", b[16:24]) if b[:8] == b"\x89PNG\r\n\x1a\n" else (0, 0)
        check("%s is a %dx%d PNG" % (path, size, size), st == 200 and (w, hh) == (size, size), (st, w, hh))
    st, b, h = hreq("/atlas.ico")
    check("/atlas.ico is a multi-size icon (16..256)", st == 200 and b[:4] == b"\x00\x00\x01\x00" and struct.unpack("<H", b[4:6])[0] >= 5, (st, b[:6]))
    with sync_playwright() as p:
        br = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        ctx = br.new_context(viewport={"width": 1600, "height": 900}); pg = ctx.new_page(); errs = []; pg.on("pageerror", lambda x: errs.append(str(x)))
        pg.goto(URL); pg.wait_for_function("window.__hud&&__hud.state().online===true", timeout=20000)
        head = pg.evaluate("[...document.querySelectorAll('link[rel~=icon],link[rel=manifest]')].map(l=>l.rel+' '+l.getAttribute('href'))")
        check("HUD <head> links the .ico, the PNG icons and the manifest", any("manifest" in x for x in head) and any(".ico" in x for x in head) and any("512" in x for x in head), head)
        log.write_text("")
        popups = []; ctx.on("page", lambda pp: popups.append(pp.url))
        pg.evaluate("""(()=>{const a=document.createElement('a');a.id='tlink';a.href='https://openrouter.ai/keys';a.target='_blank';a.rel='noopener';a.textContent='Get a key';
                         a.style.cssText='position:fixed;top:40%;left:40%;z-index:99999;font-size:30px;background:#000';document.body.appendChild(a)})()""")
        pg.click("#tlink"); pg.wait_for_timeout(1200)
        check("clicking a 'Get a key' link sends it to the default browser (bridge), not a new app-window tab",
              log.read_text().split() == ["https://openrouter.ai/keys"] and not popups, (log.read_text(), popups))
        pg.evaluate("window.open('https://auth.example.com/signin?x=1','_blank')"); pg.wait_for_timeout(1000)
        check("window.open of an outside link also goes to the default browser", "https://auth.example.com/signin?x=1" in log.read_text().split() and not popups, (log.read_text(), popups))
        pg.evaluate("ATLAS.openUrl('https://example.org/')"); pg.wait_for_timeout(800)
        check("ATLAS.openUrl() (for add-ons) uses the default browser", "https://example.org/" in log.read_text().split())
        check("no page errors", not errs, errs)
        br.close()
finally: e.down()
print("FAILED: %s" % fails if fails else "ALL PASSED"); sys.exit(1 if fails else 0)
