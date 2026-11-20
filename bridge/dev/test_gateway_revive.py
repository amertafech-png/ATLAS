"""Dev-only: ATLAS keeps Hermes running.  If the Hermes gateway stops (Adam closed the window it was running in), the next chat
starts it again and answers; a background watchdog also restarts it; if it cannot be started the error says so in plain words.
Uses the mock Hermes + a fake `hermes` CLI (shim) in a temp HOME - nothing real is started or touched.
   /workspace/bridge-venv/bin/python bridge/dev/test_gateway_revive.py"""
import subprocess, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
src = (HERE / "test_setup_e2e.py").read_text().split("# ---------------------------------------------------------------- 1.")[0]
exec(compile(src, "test_setup_e2e_prefix", "exec"))
KEY = "atlas-" + "0123456789abcdef" * 3

def kill_hermes():
    subprocess.call(["pkill", "-f", "mock_hermes.py %d" % MOCK_PORT]); time.sleep(1)
def hermes_up(): return req("GET", "/health", port=MOCK_PORT)[0] == 200

print("== 1. Hermes stops while ATLAS runs -> the next question starts it again")
e = Env("revive", env_text="API_SERVER_ENABLED=true\nAPI_SERVER_KEY=%s\n" % KEY)
try:
    check("bridge starts", e.up())
    c, j = req("POST", "/api/chat", {"text": "hello"}); check("chat works", c == 200 and j.get("ok"), j)
    kill_hermes(); check("(Hermes is really stopped)", not hermes_up())
    t0 = time.time(); c, j = req("POST", "/api/chat", {"text": "are you there"})
    check("the next chat restarts Hermes and still answers", c == 200 and j.get("ok") and "Mock Hermes heard" in j.get("reply", ""), j)
    check("...within the chat request (%.0fs)" % (time.time() - t0), time.time() - t0 < 30)
    check("log says why it restarted Hermes", "not answering (chat)" in e.log() and "is up again" in e.log())
    print("   waiting for the watchdog (up to 3 min)...")
    kill_hermes()
    ok = wait(hermes_up, 180, 2)
    check("the watchdog restarts Hermes by itself, with no chat", ok and "not answering (watchdog)" in e.log(), e.log()[-400:])
finally: e.down()

print("== 2. Hermes cannot be started -> plain words + Fix it")
e = Env("nostart", env_text="API_SERVER_ENABLED=true\nAPI_SERVER_KEY=%s\n" % KEY)
try:
    check("bridge starts", e.up())
    (e.bin / "hermes").write_text("#!/bin/bash\necho 'fake hermes that cannot start the gateway'; exit 1\n")
    kill_hermes()
    c, j = req("POST", "/api/chat", {"text": "hello"})
    err = j.get("error", "")
    check("friendly error with Fix it, not the old 'Can't reach the Hermes API at ... ClientConnectorError' text", c == 503 and j.get("fix") == "setup" and "ClientConnectorError" not in err and "API_SERVER_ENABLED" not in err and "Hermes" in err, j)
    check("...says it is not running / being started", "isn't running" in err or "starting it" in err or "had stopped" in err, err)
finally: e.down()

print("== 3. tests and ATLAS_NO_BOOT never start Hermes")
e = Env("noboot", env_text="API_SERVER_ENABLED=true\nAPI_SERVER_KEY=%s\n" % KEY)
e.env["ATLAS_NO_BOOT"] = "1"
try:
    e.up(); kill_hermes()
    c, j = req("POST", "/api/chat", {"text": "hello"})
    check("with ATLAS_NO_BOOT nothing is started; the error says to press Fix it", c == 503 and not hermes_up() and "Fix it" in j.get("error", ""), j)
finally: e.down()
print("FAILED: %s" % fails if fails else "ALL PASSED"); sys.exit(1 if fails else 0)
