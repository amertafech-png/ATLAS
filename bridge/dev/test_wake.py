"""Wake-word tests (dev only).  Needs:  pip install vosk piper-tts aiohttp numpy   (numpy comes with piper).

  python bridge/dev/test_wake.py [--work DIR] [--voices N] [--quick]

Builds test audio on the fly with Piper TTS (voices are downloaded into --work on first run, ~65 MB each; nothing is
committed to the repo), downloads the Vosk model the same way the feature does (or uses $ATLAS_WAKE_MODEL), then:
  1. matcher unit tests (text level, incl. renamed phrase, aliases, unknown names)
  2. spotter tests: spoken "Hey Atlas" / "Hey Nova" (several voices, speeds, noise, low volume) must trigger, other speech must not
  3. integration: starts a real bridge, enables the feature, streams audio over the WebSocket exactly like the HUD,
     checks the SSE "wake" event, renaming the assistant (Atlas -> Nova), mute, and reports CPU per second of audio
"""
import argparse, asyncio, io, json, os, subprocess, sys, tempfile, time, wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
BRIDGE = HERE.parent
sys.path.insert(0, str(BRIDGE))
import numpy as np                                    # noqa: E402
import importlib.util                                 # noqa: E402

spec = importlib.util.spec_from_file_location("wake_word", BRIDGE / "modules" / "wake_word.py")
ww = importlib.util.module_from_spec(spec); spec.loader.exec_module(ww)

ap = argparse.ArgumentParser()
ap.add_argument("--work", default=os.path.join(tempfile.gettempdir(), "atlas-wake-test"))
ap.add_argument("--voices", type=int, default=2, help="how many Piper voices to use (1-4)")
ap.add_argument("--quick", action="store_true")
args = ap.parse_args()
WORK = Path(args.work); WORK.mkdir(parents=True, exist_ok=True)
VOICES = ["en_US-lessac-medium", "en_US-ryan-medium", "en_US-amy-medium", "en_US-libritts_r-medium"][:max(1, min(4, args.voices))]

results = []
def check(name, ok, detail=""):
    results.append(ok); print(("PASS " if ok else "FAIL ") + name + (" | " + detail if detail else ""), flush=True)

# ---------------------------------------------------------------- 1. matcher
M = ww.Matcher
check("matcher: exact phrase", M("Hey ATLAS").match("hey atlas"))
check("matcher: followed by a command", M("Hey ATLAS").match("hey atlas what is the weather"))
check("matcher: leading filler word", M("Hey ATLAS").match("um hey atlas"))
check("matcher: hay/he variants of 'hey'", M("Hey ATLAS").match("hay atlas") and M("Hey ATLAS").match("he atlas"))
check("matcher: renamed phrase", M("Hey Nova").match("hey nova play music") and not M("Hey Nova").match("hey atlas"))
check("matcher: the old product name does not wake ATLAS", not M("Hey ATLAS").match("hey modi"))   # legacy-modi
check("matcher: mid-sentence mention is ignored", not M("Hey ATLAS").match("i told him that hey atlas is a nice name"))
check("matcher: similar word is not enough for a known name", not M("Hey ATLAS").match("hey molly") and not M("Hey ATLAS").match("hey atlantic") and not M("Hey ATLAS").match("hey atlanta"))
check("matcher: alias (sounds-like) works", M("Hey ATLAS", aliases=["at last"]).match("hey at last"))
check("matcher: unknown name matches fuzzily", M("Hey Zorbeck", oov=lambda w: True).match("hey zorbec") and not M("Hey Zorbeck", oov=lambda w: True).match("hey zebra"))
check("matcher: digits in names", M("Hey Jarvis 2").match("hey jarvis two"))
check("matcher: empty text", not M("Hey ATLAS").match(""))

# ---------------------------------------------------------------- audio fixtures (Piper)
def synth_all():
    from piper import PiperVoice, SynthesisConfig
    vd = WORK / "voices"; vd.mkdir(exist_ok=True)
    for v in VOICES:
        if not (vd / f"{v}.onnx").exists():
            print("downloading Piper voice", v, flush=True)
            subprocess.run([sys.executable, "-m", "piper.download_voices", v, "--download-dir", str(vd)], check=True, capture_output=True)
    rng = np.random.default_rng(7)
    out = {"pos_atlas": [], "pos_nova": [], "neg": []}
    POS = {"pos_atlas": ["Hey Atlas", "Hey Atlas, what is the weather today"], "pos_nova": ["Hey Nova", "Hey Nova, play some music"]}
    NEG = ["Hello there, how are you", "What time is it", "The weather is nice today", "Hey Molly, come over here",
           "Okay computer, open the door", "Today we talked about modern design", "Hey buddy how are you doing",
           "I need a new phone", "Hey Noah can you hear me", "A nova is a star that suddenly brightens",
           "Please remind me to call mom", "Hey Atlantic City is a nice town", "Atlas is a name I have heard before",
           "Turn the lights off in the kitchen", "Hey Mommy, look at this"]
    def pcm(v, text, ls):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            v.synthesize_wav(text, w, syn_config=SynthesisConfig(length_scale=ls))
        buf.seek(0)
        with wave.open(buf) as w:
            sr = w.getframerate(); a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32)
        n = int(len(a) * 16000 / sr)
        return np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a)
    def finish(x, gain=1.0, snr=None):
        x = x * gain
        if snr:
            noise = rng.normal(0, 1, len(x)); p = np.mean(x ** 2) ** .5
            x = x + noise * p / (10 ** (snr / 20))
        pad = np.zeros(6000)
        return np.concatenate([pad, x, pad]).clip(-32768, 32767).astype("<i2")
    for vn in VOICES:
        v = PiperVoice.load(str(vd / f"{vn}.onnx"))
        for k, ps in POS.items():
            for p in ps:
                for ls in ((0.9, 1.1) if not args.quick else (1.0,)):
                    out[k].append((f"{vn}/{p}/{ls}", finish(pcm(v, p, ls))))
                out[k].append((f"{vn}/{p}/noisy", finish(pcm(v, p, 1.0), snr=15)))
                out[k].append((f"{vn}/{p}/quiet", finish(pcm(v, p, 1.0), gain=0.25)))
        for t in NEG[: (6 if args.quick else None)]:
            out["neg"].append((f"{vn}/{t}", finish(pcm(v, t, 1.0))))
    return out

t0 = time.time(); AUD = synth_all(); print(f"fixtures: {len(AUD['pos_atlas'])} 'Hey Atlas', {len(AUD['pos_nova'])} 'Hey Nova', {len(AUD['neg'])} other speech ({time.time()-t0:.0f}s)")

# ---------------------------------------------------------------- Vosk model
class FakeCtx:                                         # just enough of Ctx for the module helpers
    def __init__(self): self.data_dir = WORK / "feature"
ctx0 = FakeCtx()
mdir = ww.model_dir(ctx0)
if not ww.model_ready(mdir):
    print("downloading Vosk model ...", flush=True)
    ww.download_model(mdir, lambda f, t: None, lambda: False)
os.environ["ATLAS_WAKE_MODEL"] = str(mdir)
from vosk import Model, SetLogLevel                    # noqa: E402
SetLogLevel(-1); model = Model(str(mdir))

def run_spotter(phrase, pcm, aliases=()):
    sp = ww.Spotter(model, lambda: phrase, lambda: aliases)
    b = pcm.tobytes(); hit = False
    for i in range(0, len(b), 3200):                   # 100 ms chunks, like the HUD
        hit = sp.feed(b[i:i + 3200]) or hit
    hit = sp.end() or hit
    return hit, sp

def rate(label, phrase, items, want):
    hits, cpu, aud, missed = 0, 0.0, 0.0, []
    for name, pcm in items:
        h, sp = run_spotter(phrase, pcm); hits += h; cpu += sp.cpu_s; aud += sp.audio_s
        if h != want: missed.append(name)
    return hits, len(items), cpu / max(aud, 1e-9), missed

# ---------------------------------------------------------------- 2. spotter on spoken audio
h, n, rtf, miss = rate("pos", "Hey ATLAS", AUD["pos_atlas"], True)
check(f"spotter: 'Hey ATLAS' spoken ({n} clips: voices x speeds x noise/quiet)", h / n >= 0.9, f"{h}/{n} triggered, CPU {rtf*100:.0f}% of real time while speech is present" + (f"; missed {miss[:3]}" if miss else ""))
h, n, rtf, miss = rate("pos", "Hey Nova", AUD["pos_nova"], True)
check(f"spotter: renamed phrase 'Hey Nova' spoken ({n} clips; Piper adds randomness, so >=75% is required)", h / n >= 0.75, f"{h}/{n} triggered" + (f"; missed {miss[:3]}" if miss else ""))
h, n, rtf, miss = rate("neg", "Hey ATLAS", AUD["neg"], False)
check(f"spotter: other speech does not trigger 'Hey ATLAS' ({n} clips)", h == 0, f"{h}/{n} false triggers" + (f": {miss[:3]}" if miss else ""))
h, n, rtf, miss = rate("neg", "Hey Nova", AUD["neg"], False)
check(f"spotter: other speech does not trigger 'Hey Nova' ({n} clips)", h == 0, f"{h}/{n} false triggers" + (f": {miss[:3]}" if miss else ""))
h, n, _, miss = rate("x", "Hey Nova", AUD["pos_atlas"], False)
check("spotter: 'Hey Atlas' does not trigger after renaming to Nova", h == 0, f"{h}/{n} false triggers")
h, n, _, _ = rate("x", "Hey ATLAS", AUD["pos_nova"], False)
check("spotter: 'Hey Nova' does not trigger 'Hey ATLAS'", h == 0, f"{h}/{n} false triggers")
silence = np.zeros(16000 * 5, dtype="<i2"); noise = (np.random.default_rng(1).normal(0, 300, 16000 * 5)).astype("<i2")
check("spotter: silence / hiss does not trigger", not run_spotter("Hey ATLAS", silence)[0] and not run_spotter("Hey ATLAS", noise)[0])

# ---------------------------------------------------------------- 2b. model download (local fake server: progress, checksum, zip-slip)
def download_tests():
    import hashlib, http.server, threading, zipfile, shutil
    d = WORK / "dl"; shutil.rmtree(d, ignore_errors=True); d.mkdir()
    def mkzip(path, evil=False):
        with zipfile.ZipFile(path, "w") as z:
            z.writestr(ww.MODEL_NAME + "/am/final.mdl", b"x" * 300000); z.writestr(ww.MODEL_NAME + "/conf/model.conf", "--a=1")
            if evil: z.writestr("../../escape.txt", "no")
    mkzip(d / "good.zip"); mkzip(d / "evil.zip", True)
    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k): super().__init__(*a, directory=str(d), **k)
        def log_message(self, *a): pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/"
    keep = (ww.MODEL_URL, ww.MODEL_SHA256)
    try:
        ww.MODEL_URL = base + "good.zip"; ww.MODEL_SHA256 = hashlib.sha256((d / "good.zip").read_bytes()).hexdigest()
        seen = []; dest = d / "out" / "m"
        ww.download_model(dest, lambda f, t: seen.append((round(f, 2), t)), lambda: False)
        check("download: good archive is unpacked, progress is reported", ww.model_ready(dest) and seen[-1][0] == 1.0 and any(0 < f < 1 for f, _ in seen), f"{len(seen)} progress updates, last '{seen[-1][1]}'")
        ww.MODEL_SHA256 = "0" * 64
        try: ww.download_model(d / "out" / "m2", lambda f, t: None, lambda: False); bad = False
        except RuntimeError as e: bad = "SHA-256" in str(e)
        check("download: wrong checksum is refused and nothing is installed", bad and not (d / "out" / "m2").exists())
        ww.MODEL_URL = base + "evil.zip"; ww.MODEL_SHA256 = hashlib.sha256((d / "evil.zip").read_bytes()).hexdigest()
        try: ww.download_model(d / "out" / "m3", lambda f, t: None, lambda: False); bad = False
        except RuntimeError as e: bad = "Unsafe" in str(e)
        check("download: archive with a path-traversal entry is refused", bad and not (d / "escape.txt").exists() and not (d.parent / "escape.txt").exists())
        ww.MODEL_URL = "http://127.0.0.1:9/none.zip"
        try: ww.download_model(d / "out" / "m4", lambda f, t: None, lambda: False); off = False
        except OSError: off = True
        check("download: offline -> clean error (OSError) instead of a crash", off)
    finally:
        ww.MODEL_URL, ww.MODEL_SHA256 = keep; srv.shutdown()
download_tests()

# ---------------------------------------------------------------- 3. integration with a real bridge
async def integration():
    import aiohttp
    import socket
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0)); port = so.getsockname()[1]
    home = WORK / "home"; import shutil; shutil.rmtree(home, ignore_errors=True); home.mkdir(parents=True)
    (home / "config.json").write_text(json.dumps({"port": port, "features": {"wake_word": {"enabled": True, "settings": {}}}}))
    env = dict(os.environ, ATLAS_HOME=str(home), ATLAS_UPDATE_API="http://127.0.0.1:9", HERMES_API_KEY="test-key", ATLAS_WAKE_MODEL=str(mdir))
    proc = subprocess.Popen([sys.executable, str(BRIDGE / "bridge.py")], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{port}"
    try:
        async with aiohttp.ClientSession() as s:
            st = None
            for _ in range(120):
                try:
                    async with s.get(base + "/api/m/wake_word/status") as r:
                        if r.status == 200:
                            st = await r.json()
                            if st["state"] == "ready": break
                except aiohttp.ClientError: pass
                await asyncio.sleep(0.5)
            check("bridge: feature starts and model loads", bool(st) and st["state"] == "ready", json.dumps({k: st[k] for k in ("state", "phrase", "muted", "engine")}) if st else "no status")
            events = []
            async def sse():
                async with s.get(base + "/api/events") as r:
                    async for line in r.content:
                        if line.startswith(b"data: "):
                            events.append(json.loads(line[6:]))
            sse_task = asyncio.create_task(sse()); await asyncio.sleep(0.5)

            def gate(b):
                """Python port of the HUD's speech gate (wake_word.js onChunk): yields ('chunk', bytes) / ('end',) like the browser sends."""
                floor, ring, hot, speaking, since, n = 0.004, [], 0, False, 0, 0
                for i in range(0, len(b), 3200):
                    c = b[i:i + 3200]
                    if len(c) < 3200: c = c + bytes(3200 - len(c))
                    rms = float(np.sqrt(np.mean((np.frombuffer(c, "<i2") / 32768.0) ** 2)))
                    if rms < floor * 1.6: floor = floor * 0.97 + rms * 0.03
                    elif not speaking: floor = min(0.03, floor * 1.001)
                    floor = max(0.002, floor); loud = rms > max(0.012, floor * 3.2)
                    if not speaking:
                        ring.append(c); ring = ring[-4:]; hot = hot + 1 if loud else 0
                        if hot >= 2:
                            speaking, since, n = True, 0, 0
                            for r in ring: yield ("chunk", r)
                            ring = []
                    else:
                        yield ("chunk", c); n += 1; since = 0 if loud else since + 1
                        if since >= 7 or n >= 80:
                            yield ("end",); speaking = False; hot = 0; ring = []
                if speaking: yield ("end",)

            async def stream(pcm):
                """Send like the HUD does (same speech gate).  Returns True if the server said 'wake'."""
                woke = False
                async with s.ws_connect(base + "/api/m/wake_word/ws") as ws:
                    first = await ws.receive_json()
                    assert first["type"] == "ready", first
                    for item in gate(pcm.tobytes()):
                        if item[0] == "chunk": await ws.send_bytes(item[1])
                        else: await ws.send_json({"end": True})
                        try:
                            m = await ws.receive_json(timeout=0.002)
                            woke = woke or m.get("type") == "wake"
                        except asyncio.TimeoutError: pass
                    try:
                        m = await ws.receive_json(timeout=1.5); woke = woke or m.get("type") == "wake"
                    except asyncio.TimeoutError: pass
                return woke

            atlas = AUD["pos_atlas"][0][1]; nova = AUD["pos_nova"][0][1]; other = AUD["neg"][0][1]
            events.clear(); w = await stream(atlas); await asyncio.sleep(0.3)
            check("integration: 'Hey Atlas' -> websocket wake + SSE 'wake' event", w and any(e["type"] == "wake" for e in events))
            events.clear(); w = await stream(other); await asyncio.sleep(0.3)
            check("integration: other speech -> nothing", not w and not any(e["type"] == "wake" for e in events))
            # rename the assistant exactly like HUD Settings does
            async with s.post(base + "/api/profile", json={"assistant": {"name": "Nova"}}) as r: j = await r.json()
            async with s.get(base + "/api/m/wake_word/status") as r: st = await r.json()
            check("integration: renaming the assistant changes the phrase", j.get("assistant", {}).get("wake_phrase") == "Hey Nova" and st["phrase"] == "Hey Nova", st["phrase"])
            await asyncio.sleep(2.6)                     # cooldown between triggers
            events.clear(); w = await stream(nova); await asyncio.sleep(0.3)
            check("integration: 'Hey Nova' wakes it after the rename", w and any(e["type"] == "wake" and e.get("phrase") == "Hey Nova" for e in events))
            await asyncio.sleep(2.6)
            w = await stream(atlas)
            check("integration: old name 'Hey Atlas' no longer wakes it", not w)
            # mute
            async with s.post(base + "/api/m/wake_word/mute", json={"muted": True}) as r: st = await r.json()
            await asyncio.sleep(2.6); events.clear(); w = await stream(nova)
            check("integration: muted -> no trigger", st["muted"] and not w and not any(e["type"] == "wake" for e in events))
            async with s.post(base + "/api/m/wake_word/mute", json={"muted": False}) as r: st = await r.json()
            await asyncio.sleep(2.6); w = await stream(nova)
            check("integration: unmuted -> triggers again", (not st["muted"]) and w)
            # alias
            async with s.post(base + "/api/m/wake_word/aliases", json={"aliases": ["Nover"]}) as r: st = await r.json()
            check("integration: aliases saved", st["aliases"] == ["Nover"])
            async with s.get(base + "/api/m/wake_word/status") as r: st = await r.json()
            print(f"INFO bridge-side: {st['audio_s']} s of audio processed using {st['cpu_s']} s CPU")
            sse_task.cancel()
    finally:
        proc.terminate()
        try: proc.wait(8)
        except subprocess.TimeoutExpired: proc.kill()

if not args.quick or True:
    asyncio.run(integration())

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
