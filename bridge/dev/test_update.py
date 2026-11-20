"""Unit-style tests for atlas_update against the mock release server (installer launch is stubbed)."""
import asyncio, os, subprocess, sys, tempfile, time, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import atlas_meta, atlas_update
os.environ['ATLAS_UPDATE_PLATFORM'] = 'mac'
DIST = str(Path(__file__).resolve().parents[2] / 'dist')
PORT = 9912
def start(mode, tag='v1.1.0'):
    p = subprocess.Popen([sys.executable, str(Path(__file__).parent / 'mock_release.py'), str(PORT), DIST, '--tag', tag, '--mode', mode])
    time.sleep(1.2); return p
async def case(name, mode, expect_check, expect_install, current='1.0.0', api=None, enabled=True):
    p = start(mode)
    try:
        atlas_meta.VERSION = current
        d = Path(tempfile.mkdtemp()); launched = []
        os.environ['ATLAS_UPDATE_API'] = api or f'http://127.0.0.1:{PORT}'
        u = atlas_update.Updater({'update': {'enabled': enabled}}, d, d / 'config.json', lambda *a, **k: None, lambda: None)
        u._launch = lambda plat, path: launched.append(str(path))
        st = await u.check(force=True)
        ok1 = (st['status'] == expect_check)
        res = 'n/a'
        if expect_install is not None:
            try:
                await u.install(); res = 'installed'
            except RuntimeError as e:
                res = 'refused: ' + str(e)
            ok2 = (res.startswith('installed') if expect_install else res.startswith('refused'))
            ok2 = ok2 and (bool(launched) == expect_install)
        else:
            ok2 = True
        print(('PASS' if ok1 and ok2 else 'FAIL'), name, '| check:', st['status'], '|', st['message'], '| install:', res)
    finally:
        p.terminate(); p.wait()
async def main():
    await case('good release (SHA256SUMS asset)', 'ok', 'available', True)
    await case('checksums only in release notes', 'notesum'.replace('notesum', 'notesums'), 'available', True)
    await case('checksum mismatch', 'badsum', 'available', False)
    await case('no checksum published', 'nosums', 'available', False)
    await case('same/older release tag', 'older', 'up_to_date', None)
    await case('same version', 'ok', 'up_to_date', None, current='1.1.0')
    await case('no releases yet (404)', '404', 'up_to_date', None)
    await case('server error 500', '500', 'error', None)
    await case('rate limited 403', 'ratelimit', 'error', None)
    await case('offline / unreachable', 'ok', 'error', None, api='http://127.0.0.1:9')
    await case('checks disabled (no forced check ignores)', 'ok', 'available', None, enabled=False)
    # caching + ETag
    p = start('ok')
    try:
        atlas_meta.VERSION = '1.0.0'; os.environ['ATLAS_UPDATE_API'] = f'http://127.0.0.1:{PORT}'
        d = Path(tempfile.mkdtemp())
        u = atlas_update.Updater({'update': {'enabled': True}}, d, d / 'config.json', lambda *a, **k: None, lambda: None)
        await u.check(); await u.check(); await u.check(startup=True)
        import urllib.request
        h1 = json.load(urllib.request.urlopen(f'http://127.0.0.1:{PORT}/stats'))
        u2 = atlas_update.Updater({'update': {'enabled': True}}, d, d / 'config.json', lambda *a, **k: None, lambda: None)  # "restart" with warm cache
        await u2.check(startup=True)
        h2 = json.load(urllib.request.urlopen(f'http://127.0.0.1:{PORT}/stats'))
        # stale cache -> conditional request gets 304
        u2._cache['checked_at'] -= 13 * 3600; u2._last_net = 0
        await u2.check()
        h3 = json.load(urllib.request.urlopen(f'http://127.0.0.1:{PORT}/stats'))
        print('PASS' if (h1['releases'] == 1 and h2['releases'] == 1 and h3['releases'] == 2 and h3['etag304'] == 1 and u2.state()['available']) else 'FAIL',
              'cache: 3 checks + restart = 1 request; after 13h = conditional request ->', h3)
    finally:
        p.terminate(); p.wait()
    # host allow-list on the official API
    os.environ.pop('ATLAS_UPDATE_API', None)
    d = Path(tempfile.mkdtemp())
    u = atlas_update.Updater({'update': {}}, d, d / 'config.json', lambda *a, **k: None, lambda: None)
    class S: pass
    try:
        await u._http_get(None, 'https://evil.example.com/ATLAS-Setup.bat', 100); print('FAIL host check')
    except RuntimeError as e:
        print('PASS host allow-list:', e)
    try:
        await u._http_get(None, 'http://github.com/x', 100); print('FAIL scheme check')
    except RuntimeError as e:
        print('PASS https-only:', e)
    print('semver:', atlas_update.is_newer('v1.10.0', '1.9.9'), atlas_update.is_newer('v1.1.0', '1.1.0'), atlas_update.is_newer('latest', '1.0.0'), atlas_update.is_newer('v2.0.0-beta.1', '1.9.0'))
asyncio.run(main())
