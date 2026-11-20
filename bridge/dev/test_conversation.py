"""Dev-only: Conversation mode (follow-up listening window) in the real HUD, headless Chrome vs a real bridge.
   Microphone = a fake MediaStream whose loudness the test controls; /api/listen, /api/chat and /api/speak are mocked so the test controls the
   transcripts and the length of the spoken reply. Checks timing (mic opens only AFTER the reply audio ended), tone, countdown ring, quiet stop,
   end phrases, Esc / button, typed requests, wake-word path, settings, persistence.
   /workspace/pwenv/bin/python bridge/dev/test_conversation.py [--shots]      -> docs/conversation-*.png"""
import io, json, struct, sys, time, wave
from pathlib import Path
from playwright.sync_api import sync_playwright
HERE = Path(__file__).resolve().parent; DOCS = HERE.parent.parent / "docs"; SHOTS = "--shots" in sys.argv
src = (HERE / "test_setup_e2e.py").read_text().split("# ---------------------------------------------------------------- 1.")[0]
exec(compile(src, "test_setup_e2e_prefix", "exec"))
PY = "/workspace/bridge-venv/bin/python"; URL = "http://127.0.0.1:%d/" % BR_PORT

def wav_bytes(secs=0.9):
    b = io.BytesIO(); w = wave.open(b, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes(struct.pack("<h", 0) * int(16000 * secs)); w.close(); return b.getvalue()
WAV = wav_bytes(0.9)

INIT = """
(()=>{
  const AC=window.AudioContext; window.__mic={opens:[],level:0,gains:[],tones:0,ended:[],play:[]};
  navigator.mediaDevices.getUserMedia=async(c)=>{
    if(!(c&&c.audio))return Promise.reject(new Error('no video in test'));
    window.__mic.opens.push(performance.now());
    const ctx=new AC();await ctx.resume();const o=ctx.createOscillator();o.frequency.value=220;const g=ctx.createGain();g.gain.value=window.__mic.level;window.__mic.gains.push(g);
    o.connect(g);const d=ctx.createMediaStreamDestination();g.connect(d);o.start();return d.stream};
  window.__mic.set=v=>{window.__mic.level=v;window.__mic.gains.forEach(g=>g.gain.value=v)};
  const oc=AC.prototype.createOscillator;AC.prototype.createOscillator=function(){window.__mic.tones++;return oc.apply(this,arguments)};
  const pl=HTMLMediaElement.prototype.play;HTMLMediaElement.prototype.play=function(){window.__mic.play.push(performance.now());if(!this.__t){this.__t=1;this.addEventListener('ended',()=>window.__mic.ended.push(performance.now()))}return pl.apply(this,arguments)};
})();"""

class Mock:
    def __init__(self): self.listen = []; self.chat = []; self.speak_calls = 0; self.heard = ["what time is it"]; self.speak_fail = False
    def install(self, pg):
        def listen(r):
            self.listen.append(time.time()); t = self.heard.pop(0) if self.heard else ""
            r.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "transcript": t, "device": "test", "ms": 5}))
        def chat(r):
            txt = json.loads(r.request.post_data or "{}").get("text", ""); self.chat.append(txt)
            r.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "reply": "Answer to: " + txt, "ms": 10}))
        def speak(r):
            self.speak_calls += 1; r.fulfill(status=200, content_type="audio/wav", body=WAV)
        pg.route("**/api/listen", listen); pg.route("**/api/chat", chat); pg.route("**/api/speak", speak)

e = Env("conv", env_text="OPENROUTER_API_KEY=placeholder-not-a-secret\n")
try:
    e.env["MOCK_HERMES_HOME"] = str(e.hh); e.up()
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required"])
        def fresh(secs=3, on=True):
            ctx = b.new_context(viewport={"width": 1600, "height": 900}); ctx.add_init_script(INIT)
            pg = ctx.new_page(); m = Mock(); m.install(pg); errs = []; pg.on("pageerror", lambda x: errs.append(str(x)))
            pg.goto(URL); pg.wait_for_function("window.__hud&&__hud.state().online===true", timeout=20000)
            pg.wait_for_timeout(1600)      # the HUD may auto-open its Hermes setup card (no real Hermes here); close it
            pg.evaluate("__hud.CONV.secs=%d;__hud.CONV.on=%s;__hud.SU.dismissed=true;__hud.closeSetup()" % (secs, "true" if on else "false")); pg.wait_for_timeout(300)
            return pg, m, errs
        def say(pg, ms=900):          # user speaks into the (fake) mic for ms
            pg.evaluate("__mic.set(0.5)"); pg.wait_for_timeout(ms); pg.evaluate("__mic.set(0)")
        def state(pg): return pg.evaluate("__hud.CONV.state")
        def orb(pg): return pg.evaluate("__hud.state().orb")
        def ptt(pg, ms=900):          # push-to-talk turn
            pg.evaluate("__mic.level=0"); pg.click("#mic2"); pg.wait_for_function("__hud.state().orb==='listening'", timeout=5000); say(pg, ms)

        # ---- 0. pure logic + defaults ----
        pg, m, errs = fresh()
        d = b.new_context(); dp = d.new_page(); dp.goto(URL); dp.wait_for_function("window.__hud&&!!__hud.CONV")
        check("defaults: on, 6 s", dp.evaluate("__hud.CONV.on===true&&__hud.CONV.secs===6"), dp.evaluate("JSON.stringify(__hud.CONV)")); d.close()
        ends = ["that's all", "That is all.", "stop", "Thanks", "thank you", "Thanks, ATLAS!", "okay thanks", "ok that's all", "no thanks", "never mind", "that's it", "Stop listening"]
        keeps = ["what about tomorrow", "stop the music and play jazz", "thanks for the weather but what about Friday", "tell me more", "that's all wrong, explain again"]
        check("end phrases recognised", all(pg.evaluate("t=>__hud.convIsEnd(t)", t) for t in ends), [t for t in ends if not pg.evaluate("t=>__hud.convIsEnd(t)", t)])
        check("normal replies are NOT end phrases", not any(pg.evaluate("t=>__hud.convIsEnd(t)", t) for t in keeps), [t for t in keeps if pg.evaluate("t=>__hud.convIsEnd(t)", t)])

        # ---- 1. push-to-talk answer -> window opens only after the reply audio ended ----
        pg.evaluate('__hud.CONV.secs=6')
        ptt(pg); 
        pg.wait_for_function("__mic.ended.length>=1", timeout=15000)
        ended_at = pg.evaluate("__mic.ended[0]")
        check("no mic while ATLAS was speaking (the only mic open before the reply ended is the push-to-talk one)", pg.evaluate("__mic.opens.filter(t=>t<__mic.ended[0]).length") == 1 and pg.evaluate("__mic.opens[0]") < pg.evaluate("__mic.play[0]"))
        pg.wait_for_function("__hud.CONV.state==='listening'", timeout=5000)
        opened_at = pg.evaluate("__mic.opens[1]")
        check("mic reopened >= 350 ms AFTER the reply audio ended", opened_at - ended_at >= 350, round(opened_at - ended_at))
        check("...and soon after (< 1.5 s)", opened_at - ended_at < 1500, round(opened_at - ended_at))
        check("soft tone played before the window", pg.evaluate("__mic.tones") >= 2, pg.evaluate("__mic.tones"))
        check("countdown ring visible + shows seconds", pg.evaluate("document.body.dataset.conv==='listening'") and bool(__import__('re').search(r"(?i)reply now · [1-6] s", pg.inner_text("#convpill"))), pg.inner_text("#convpill"))
        check("orb shows listening", orb(pg) == "listening")
        pg.wait_for_timeout(1200)
        off = float(pg.get_attribute("#convarc", "stroke-dashoffset"))
        check("ring is draining", 40 < off < 250, off)
        if SHOTS: pg.screenshot(path=str(DOCS / "conversation-ring.png"))
        # ---- 2. nobody speaks -> stops quietly ----
        n_listen = len(m.listen); cap_before = ""
        pg.wait_for_function("__hud.CONV.state==='off'", timeout=8000); pg.wait_for_timeout(600)
        check("silence: window closed by itself", state(pg) == "off" and orb(pg) == "idle")
        check("silence: nothing sent to speech-to-text, no second chat", len(m.listen) == n_listen and len(m.chat) == 1, (len(m.listen), n_listen, m.chat))
        cap = pg.evaluate("document.getElementById('caption').innerText")
        check("silence: quiet (no 'I didn't hear anything' message)", "didn't hear" not in cap.lower() and "didn't catch" not in cap.lower(), cap)
        check("silence: ring gone", pg.evaluate("document.body.dataset.conv===undefined"))
        pg.context.close()

        # ---- 3. reply inside the window -> chain continues; 'thanks' ends it ----
        pg, m, errs = fresh(); m.heard = ["what time is it", "and tomorrow", "thanks"]
        ptt(pg); pg.wait_for_function("__hud.CONV.state==='listening'", timeout=15000)
        pg.evaluate("__mic.set(0.5)")
        pg.wait_for_function("document.body.dataset.conv==='heard'", timeout=3000)
        check("speech starts -> ring switches to 'hearing you'", "hearing you" in pg.inner_text("#convpill").lower(), pg.inner_text("#convpill"))
        pg.wait_for_timeout(700); pg.evaluate("__mic.set(0)")
        pg.wait_for_function("__mic.ended.length>=2", timeout=15000)     # the follow-up answer was spoken too
        check("follow-up reached Hermes as its own question", m.chat == ["what time is it", "and tomorrow"], m.chat)
        pg.wait_for_function("__hud.CONV.state==='listening'", timeout=8000)   # window re-opens after the 2nd answer (chain)
        say(pg, 900); pg.wait_for_function("__hud.CONV.state==='off'&&__hud.state().orb==='idle'", timeout=10000); pg.wait_for_timeout(500)
        check("'thanks' ended the conversation without a reply", m.chat == ["what time is it", "and tomorrow"] and state(pg) == "off", m.chat)
        pg.wait_for_timeout(1500); check("no new window opened after 'thanks'", state(pg) == "off" and pg.evaluate("__mic.opens.length") == 3, pg.evaluate("__mic.opens.length"))
        check("no page errors", not errs, errs); pg.context.close()

        # ---- 4. Esc ends it ----
        pg, m, errs = fresh(); ptt(pg); pg.wait_for_function("__hud.CONV.state==='listening'", timeout=15000)
        pg.keyboard.press("Escape"); pg.wait_for_function("__hud.CONV.state==='off'", timeout=3000); pg.wait_for_timeout(800)
        check("Esc ends the window quietly", state(pg) == "off" and orb(pg) == "idle" and len(m.listen) == 1 and "Cancelled" not in pg.evaluate("document.getElementById('caption').innerText"))
        pg.context.close()
        # ---- 5. mic button ends it ----
        pg, m, errs = fresh(); ptt(pg); pg.wait_for_function("__hud.CONV.state==='listening'", timeout=15000)
        pg.click("#mic2"); pg.wait_for_function("__hud.CONV.state==='off'", timeout=3000); pg.wait_for_timeout(800)
        check("mic button ends the window (nothing sent)", state(pg) == "off" and orb(pg) == "idle" and len(m.listen) == 1 and len(m.chat) == 1)
        # button during the chime (arming)
        m.heard = ["one more"]; ptt(pg); pg.wait_for_function("__hud.CONV.state==='arming'", timeout=15000); pg.click("#mic2"); pg.wait_for_timeout(900)
        check("button during the chime also ends it (mic never opens)", state(pg) == "off" and pg.evaluate("__mic.opens.length") == 3, pg.evaluate("__mic.opens.length"))
        pg.context.close()
        # ---- 6. typed request: no window; typing during a window ends it ----
        pg, m, errs = fresh(); pg.fill("#cmdin", "typed question"); pg.press("#cmdin", "Enter")
        pg.wait_for_function("__mic.ended.length>=1", timeout=15000); pg.wait_for_timeout(1500)
        check("typed question: reply spoken but no follow-up window", state(pg) == "off" and pg.evaluate("__mic.opens.length") == 0)
        m.heard = ["one more"]; ptt(pg); pg.wait_for_function("__hud.CONV.state==='listening'", timeout=15000)
        pg.fill("#cmdin", "typed during window"); pg.press("#cmdin", "Enter"); pg.wait_for_timeout(500)
        check("typing during a window ends it (and sends the typed text)", m.chat[-1] == "typed during window" and state(pg) == "off", (m.chat, state(pg)))
        pg.context.close()
        # ---- 7. toggled off / speech off ----
        pg, m, errs = fresh(on=False); ptt(pg); pg.wait_for_function("__mic.ended.length>=1", timeout=15000); pg.wait_for_timeout(1500)
        check("toggle off: no follow-up window", state(pg) == "off" and pg.evaluate("__mic.opens.length") == 1); pg.context.close()
        pg, m, errs = fresh(); pg.evaluate("__hud.setSpeak(false)"); ptt(pg); pg.wait_for_function("__hud.state().msgs.length>=2", timeout=15000); pg.wait_for_timeout(2500)
        check("spoken replies off: no follow-up window", state(pg) == "off" and pg.evaluate("__mic.opens.length") == 1); pg.context.close()
        # ---- 8. wake-word path ----
        pg, m, errs = fresh(); m.heard = ["hey atlas what's the weather"]; pg.evaluate("__mic.level=0")
        pg.evaluate("ATLAS.listen({wake:true})"); pg.wait_for_function("__hud.state().orb==='listening'", timeout=5000); say(pg, 900)
        pg.wait_for_function("__mic.ended.length>=1", timeout=15000)
        check("wake word: phrase stripped, question answered", m.chat == ["what's the weather"], m.chat)
        pg.wait_for_function("__hud.CONV.state==='arming'&&ATLAS.orb()==='listening'", timeout=5000)
        check("wake-word listener is held off while the chime plays (ATLAS.orb() reports busy)", True)
        pg.wait_for_function("__hud.CONV.state==='listening'", timeout=5000)
        check("wake word: follow-up window opened after the answer", state(pg) == "listening")
        pg.context.close()
        # ---- 9. settings UI + persistence ----
        pg, m, errs = fresh(secs=6); pg.evaluate("ATLAS.openSettings('features')"); pg.wait_for_selector("#conv-sw", timeout=5000)
        check("settings: toggle on + 6 s", "On" in pg.text_content("#convcard") and pg.input_value("#conv-range") == "6")
        pg.evaluate("(()=>{const r=document.getElementById('conv-range');r.value=12;r.dispatchEvent(new Event('input',{bubbles:true}))})()")
        check("settings: slider sets 12 s", pg.evaluate("__hud.CONV.secs") == 12 and pg.text_content("#conv-val").strip() == "12 s")
        pg.evaluate("(()=>{const r=document.getElementById('conv-range');r.value=1;r.dispatchEvent(new Event('input',{bubbles:true}))})()")
        check("settings: clamps to 3..15", pg.evaluate("__hud.CONV.secs") == 3, pg.evaluate("__hud.CONV.secs"))
        pg.evaluate("(()=>{const r=document.getElementById('conv-range');r.value=9;r.dispatchEvent(new Event('input',{bubbles:true}))})()")
        if SHOTS: pg.wait_for_timeout(300); pg.screenshot(path=str(DOCS / "conversation-settings.png"))
        pg.click("#conv-sw"); pg.wait_for_timeout(300)
        check("settings: toggle off", pg.evaluate("__hud.CONV.on") is False and "Off" in pg.text_content("#convcard"))
        pg.reload(); pg.wait_for_function("window.__hud&&__hud.state().online===true")
        check("settings persist across reloads (off, 9 s)", pg.evaluate("__hud.CONV.on===false&&__hud.CONV.secs===9"), pg.evaluate("JSON.stringify(__hud.CONV)"))
        pg.context.close(); b.close()
finally: e.down()
print("FAILED: %s" % fails if fails else "ALL PASSED"); sys.exit(1 if fails else 0)
