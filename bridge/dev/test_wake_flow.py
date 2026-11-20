"""Regression test (v1.2.1): the FULL wake-word hand-off in the real HUD, headless Chrome, real bridge, real speech-to-text (faster-whisper),
   real text-to-speech (Piper), mock Hermes API for the chat.  Fails with "Assignment to constant variable" on v1.2.0.
     wake word fires -> ATLAS.listen({wake:true}) (exactly what wake_word.js does)   -> mic records "Hey ATLAS, what time is it?"
     -> POST /api/listen (Whisper)  -> wake phrase stripped  -> POST /api/chat (Hermes)  -> POST /api/speak (Piper)  -> reply audio plays -> idle
   The microphone is a MediaStream that plays a WAV made by the bridge's own Piper voice (so Whisper hears real speech).
   /workspace/pwenv/bin/python bridge/dev/test_wake_flow.py"""
import base64, io, os, shutil, sys, time, urllib.request, wave, glob, json
from pathlib import Path
from playwright.sync_api import sync_playwright
HERE = Path(__file__).resolve().parent
src = (HERE / "test_setup_e2e.py").read_text().split("# ---------------------------------------------------------------- 1.")[0]
exec(compile(src, "test_setup_e2e_prefix", "exec"))
PY = "/workspace/bridge-venv/bin/python"; URL = "http://127.0.0.1:%d/" % BR_PORT
PHRASE = os.environ.get("WAKE_TEST_PHRASE", "Hey Atlas, what time is it?")

e = Env("wakeflow", env_text="OPENROUTER_API_KEY=placeholder-not-a-secret\n")
try:
    # real Piper voices + the Whisper model cache from this machine (no download in the test)
    for cand in (os.path.join(os.environ["HOME"], "atlas-data/voices"), os.path.expanduser("~/.local/share/ATLAS/voices"), os.path.expanduser("~/Library/Application Support/ATLAS/voices")):
        if os.path.isdir(cand) and glob.glob(cand + "/*.onnx"): shutil.copytree(cand, e.data / "voices"); break
    hf = os.environ.get("HF_HOME") or os.path.join(os.environ["HOME"], ".cache", "huggingface")
    if os.path.isdir(hf): e.env["HF_HOME"] = hf
    e.env["MOCK_HERMES_HOME"] = str(e.hh); e.up()
    r = urllib.request.Request(URL + "api/speak", data=json.dumps({"text": PHRASE}).encode(), headers={"Content-Type": "application/json"})
    speech = urllib.request.urlopen(r, timeout=120).read()
    check("bridge Piper produced the test speech", speech[:4] == b"RIFF", speech[:8])
    w = wave.open(io.BytesIO(speech)); fr, ch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes(); frames = w.readframes(n); w.close()
    pad = b"\x00" * (fr * ch * sw * 3)                      # 3 s of silence after the sentence -> the HUD's end-of-utterance detector fires
    out = io.BytesIO(); ww = wave.open(out, "wb"); ww.setnchannels(ch); ww.setsampwidth(sw); ww.setframerate(fr); ww.writeframes(frames + pad); ww.close()
    b64 = base64.b64encode(out.getvalue()).decode()
    INIT = """(()=>{window.__t={opens:0,played:0,ended:0};const AC=window.AudioContext;
      navigator.mediaDevices.getUserMedia=async(c)=>{window.__t.opens++;const ctx=new AC();await ctx.resume();const d=ctx.createMediaStreamDestination();
        const bin=atob('%s'),u=new Uint8Array(bin.length);for(let i=0;i<bin.length;i++)u[i]=bin.charCodeAt(i);
        const buf=await ctx.decodeAudioData(u.buffer);const s=ctx.createBufferSource();s.buffer=buf;s.connect(d);setTimeout(()=>s.start(),250);return d.stream};
      const pl=HTMLMediaElement.prototype.play;HTMLMediaElement.prototype.play=function(){window.__t.played++;if(!this.__w){this.__w=1;this.addEventListener('ended',()=>window.__t.ended++)}return pl.apply(this,arguments)};})();""" % b64
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required"])
        ctx = b.new_context(viewport={"width": 1600, "height": 900}); ctx.add_init_script(INIT); pg = ctx.new_page()
        errs = []; pg.on("pageerror", lambda x: errs.append(str(x))); pg.on("console", lambda m: errs.append(m.text) if m.type == "error" and "constant" in m.text.lower() else None)
        pg.goto(URL); pg.wait_for_function("__hud.state().online===true", timeout=30000)
        pg.evaluate("__hud.SU.dismissed=true;__hud.SU.open=false;document.getElementById('setup')&&document.getElementById('setup').classList.remove('open')"); pg.wait_for_timeout(300)
        # the exact call wake_word.js makes when the bridge reports a wake (after its tone)
        pg.evaluate("ATLAS.listen({wake:true})")
        pg.wait_for_function("__hud.state().orb==='listening'", timeout=10000)
        check("wake hand-off opened the mic", pg.evaluate("__t.opens") == 1)
        pg.wait_for_function("__hud.state().msgs.length>=2||__hud.state().captionErr", timeout=120000)
        cap = pg.evaluate("__hud.state().caption"); msgs = pg.evaluate("JSON.stringify(__hud.state().msgs)")
        check("no 'Assignment to constant variable' (or any page error)", not errs and "constant" not in cap.lower() and "constant" not in msgs.lower(), (errs, cap))
        M = json.loads(msgs)
        check("your question reached Hermes with the wake phrase removed", len(M) >= 1 and M[0]["role"] == "user" and "hey" not in M[0]["text"].lower() and "time" in M[0]["text"].lower(), M[:1])
        check("Hermes answered", len(M) >= 2 and M[1]["role"] == "assistant" and not M[1].get("error"), M[1:2])
        pg.wait_for_function("__t.ended>=1", timeout=60000)
        check("the answer was spoken (Piper audio played to the end)", pg.evaluate("__t.played>=1&&__t.ended>=1"))
        pg.wait_for_function("__hud.state().orb==='idle'", timeout=10000)
        check("back to idle", True)
        b.close()
finally: e.down()
print("FAILED: %s" % fails if fails else "ALL PASSED"); sys.exit(1 if fails else 0)
