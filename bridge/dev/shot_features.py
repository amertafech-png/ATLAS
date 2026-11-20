import asyncio, json, sys, time, urllib.request
from playwright.async_api import async_playwright
URL = 'http://127.0.0.1:8765'
def post(path, obj):
    r = urllib.request.Request(URL + path, data=json.dumps(obj).encode(), headers={'Content-Type': 'application/json'}, method='POST')
    return json.load(urllib.request.urlopen(r, timeout=70))
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path='/usr/bin/google-chrome', args=['--autoplay-policy=no-user-gesture-required'])
        pg = await b.new_page(viewport={'width': 1920, 'height': 1080})
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))
        pg.on('console', lambda m: errs.append(m.text) if m.type == 'error' else None)
        await pg.goto(URL); await pg.wait_for_timeout(2500)
        await pg.click('.dk[data-m=settings]'); await pg.wait_for_timeout(600)
        await pg.screenshot(path='/tmp/feat1.png')
        rows = await pg.eval_on_selector_all('.feat', 'els=>els.map(e=>e.querySelector("b").textContent+" | "+e.querySelector(".st").textContent)')
        print('\n'.join(rows))
        await pg.click('[data-feat=reminders]'); await pg.wait_for_timeout(400)  # off->on
        print('reminders on:', await pg.eval_on_selector('[data-feat=reminders]', 'e=>e.classList.contains("on")'))
        # say event
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, post, '/api/m/reminders/x', {'say': 'Your timer is done.'})
        await pg.wait_for_timeout(800)
        print('caption:', await pg.inner_text('#caption'))
        # confirm flow
        fut = loop.run_in_executor(None, post, '/api/m/reminders/x', {'confirm': True})
        await pg.wait_for_selector('#cfm.open', timeout=5000)
        await pg.screenshot(path='/tmp/feat2.png')
        await pg.click('#cfmyes')
        print('confirm result:', await fut)
        fut = loop.run_in_executor(None, post, '/api/m/reminders/x', {'confirm': True})
        await pg.wait_for_selector('#cfm.open'); await pg.click('#cfmno')
        print('decline result:', await fut)
        print('errors:', errs)
        await b.close()
asyncio.run(main())
