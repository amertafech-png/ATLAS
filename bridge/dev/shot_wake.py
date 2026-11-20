"""Dev-only: drive the HUD in headless Chrome with a fake microphone that "says" the wake phrase, and take screenshots.
Needs a running bridge (ATLAS_WAKE_MODEL=<vosk model dir> so nothing is downloaded), the mock Hermes (mock_hermes.py),
Playwright + Chrome, and a 16 kHz mono WAV with silence then "Hey Atlas" at FAKE_MIC (default /tmp/fake_wake.wav).
  python shot_wake.py [outdir]     writes wake-settings.png and wake-listening.png"""
import os, sys, time
from playwright.sync_api import sync_playwright
OUT = sys.argv[1] if len(sys.argv) > 1 else '/tmp'
MIC = os.environ.get('FAKE_MIC', '/tmp/fake_wake.wav')
with sync_playwright() as p:
    b = p.chromium.launch(executable_path='/usr/bin/google-chrome', args=['--no-sandbox', '--use-fake-ui-for-media-stream',
        '--use-fake-device-for-media-stream', f'--use-file-for-fake-audio-capture={MIC}' + ('' if os.environ.get('LOOP') else '%noloop'), '--autoplay-policy=no-user-gesture-required'])
    ctx = b.new_context(viewport={'width': 1600, 'height': 900}, permissions=['microphone'])
    pg = ctx.new_page(); errs = []; dialogs = []
    pg.on('pageerror', lambda e: errs.append(str(e)))
    pg.on('console', lambda m: errs.append(m.text) if m.type == 'error' else None)
    pg.on('dialog', lambda d: (dialogs.append(d.message), d.accept()))
    pg.goto('http://127.0.0.1:8765/'); pg.wait_for_timeout(3000)
    print('chip before:', pg.inner_text('#wakechip'))
    pg.click('.dk[data-m=settings]'); pg.wait_for_timeout(500)
    pg.click('.tab[data-tab=features]'); pg.wait_for_timeout(500)
    pg.click('[data-feat=wake_word]')                      # consent dialog -> accepted by the handler above
    print('consent dialog:', (dialogs[0][:90] + '...') if dialogs else 'NONE')
    pg.wait_for_function("document.getElementById('wakechip').classList.contains('armed')", timeout=60000)
    print('chip armed:', pg.inner_text('#wakechip'))
    pg.wait_for_timeout(800); pg.screenshot(path=os.path.join(OUT, 'wake-settings.png'))
    print('settings text:', pg.inner_text('.featx').replace('\n', ' | ')[:200])
    pg.click('#ovx')
    t0 = time.time()
    pg.wait_for_function("document.body.dataset.orb==='listening'", timeout=40000)
    print('wake -> listening after %.1fs (incl. the 7 s of silence in the fake mic)' % (time.time() - t0))
    pg.wait_for_timeout(450); pg.screenshot(path=os.path.join(OUT, 'wake-listening.png'))
    print('state:', pg.inner_text('#state'), '| log tail:', pg.inner_text('#log')[-120:].replace('\n', ' / ') if pg.query_selector('#log') else '')
    # mute via the chip
    pg.wait_for_function("document.body.dataset.orb==='idle'", timeout=40000)
    pg.click('#wakechip'); pg.wait_for_timeout(800)
    muted_txt = pg.inner_text('#wakechip'); print('chip after mute click:', muted_txt)
    pg.click('#wakechip'); pg.wait_for_timeout(1500)
    print('chip after unmute click:', pg.inner_text('#wakechip'), '|', pg.get_attribute('#wakechip','title'))
    print('errors:', errs)
    ok = bool(dialogs) and 'microphone' in dialogs[0] and not errs
    ok = ok and pg.inner_text('#wakechip').upper().startswith('HEY ') and 'MUTED' in muted_txt.upper()
    print('UI CHECK:', 'PASS' if ok else 'FAIL'); 
    sys.exit(0 if ok else 1)
    b.close()
