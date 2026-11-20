"""Dev-only: drive the HUD in headless Chrome with a fake mic/camera and take screenshots."""
import sys, json
from playwright.sync_api import sync_playwright
import os
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..') + os.sep
with sync_playwright() as p:
    b = p.chromium.launch(executable_path='/usr/bin/google-chrome', args=[
        '--no-sandbox', '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream',
        '--use-file-for-fake-audio-capture=/tmp/fake_mic.wav%noloop', '--autoplay-policy=no-user-gesture-required'])
    ctx = b.new_context(viewport={'width': 1920, 'height': 1080}, permissions=['microphone', 'camera'])
    pg = ctx.new_page()
    errs = []
    pg.on('response', lambda r: errs.append('HTTP %s %s' % (r.status, r.url)) if r.status >= 400 else None)
    pg.on('pageerror', lambda e: errs.append(str(e)))
    pg.on('console', lambda m: errs.append(m.text) if m.type == 'error' else None)
    pg.goto('http://127.0.0.1:8765/'); pg.wait_for_timeout(3500)
    print('chip:', pg.inner_text('#btxt'), '| ptt:', pg.inner_text('#ptt'))
    # voice turn
    pg.click('#mic2')
    pg.wait_for_function("document.getElementById('state').textContent.toLowerCase().includes('listen')", timeout=5000)
    pg.wait_for_timeout(1200); pg.screenshot(path='/tmp/s_listen.png')
    pg.wait_for_function("!document.getElementById('state').textContent.toLowerCase().includes('listen')", timeout=15000)
    pg.wait_for_function("document.getElementById('caption').textContent.includes('MODI')&&document.getElementById('caption').textContent.includes('Mock')", timeout=20000)
    print('caption:', pg.inner_text('#caption'))
    pg.wait_for_function("document.getElementById('state').textContent.includes('Speaking')", timeout=15000); pg.wait_for_timeout(700); print('state:', pg.inner_text('#state')); pg.screenshot(path='/tmp/s_speaking.png')
    pg.wait_for_function("document.getElementById('state').textContent.includes('Standing')", timeout=20000)
    # typed turn for continuity
    pg.fill('#cmdin', 'What did I ask you earlier?'); pg.press('#cmdin', 'Enter')
    pg.wait_for_function("document.getElementById('caption').textContent.includes('turn 2')", timeout=20000)
    print('caption2:', pg.inner_text('#caption'))
    pg.wait_for_function("document.getElementById('state').textContent.includes('Standing')", timeout=20000)
    # camera
    pg.click('#cam'); pg.wait_for_timeout(1500)
    pg.fill('#cmdin', 'What is on my desk?'); pg.click('#lookbtn')
    pg.wait_for_function("document.getElementById('caption').textContent.includes('Mock vision')", timeout=20000)
    print('look caption:', pg.inner_text('#caption'))
    pg.wait_for_function("document.getElementById('state').textContent.includes('Standing')", timeout=20000)
    pg.wait_for_timeout(1500)
    pg.screenshot(path=OUT + 'preview-v3-camera.png')
    # conversation overlay
    pg.click('#camx'); pg.click('.dk[data-m=chat]'); pg.wait_for_timeout(800)
    pg.screenshot(path=OUT + 'preview-v3.png')
    print('system tag:', pg.inner_text('#systag'), '| hermes tag:', pg.inner_text('#hmtag'))
    print('errors:', errs)
    b.close()
