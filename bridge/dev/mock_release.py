"""Mock GitHub Releases API for testing MODI's self-update.
   python mock_release.py PORT ASSET_DIR [--tag v1.1.0] [--mode ok|badsum|nosums|notesums|404|500|ratelimit|older]"""
import hashlib, json, os, sys
from aiohttp import web
port = int(sys.argv[1]); adir = sys.argv[2]
tag = sys.argv[sys.argv.index('--tag') + 1] if '--tag' in sys.argv else 'v1.1.0'
mode = sys.argv[sys.argv.index('--mode') + 1] if '--mode' in sys.argv else 'ok'
hits = {'releases': 0, 'etag304': 0}
def sha(n): return hashlib.sha256(open(os.path.join(adir, n), 'rb').read()).hexdigest()
async def latest(request):
    hits['releases'] += 1
    if mode == '404': return web.json_response({'message': 'Not Found'}, status=404)
    if mode == '500': return web.Response(status=500)
    if mode == 'ratelimit': return web.Response(status=403, headers={'X-RateLimit-Remaining': '0'}, text='rate limited')
    et = '"abc123"'
    if request.headers.get('If-None-Match') == et:
        hits['etag304'] += 1; return web.Response(status=304, headers={'ETag': et})
    base = f'http://127.0.0.1:{port}/dl'
    assets = [{'name': n, 'size': os.path.getsize(os.path.join(adir, n)), 'browser_download_url': f'{base}/{n}'} for n in ('MODI-Setup.bat', 'MODI-Setup.command')]
    body = f"## MODI {tag}\n\n- New: self-update\n- Fix: things\n"
    if mode != 'nosums':
        if mode == 'notesums':
            body += "\n```\n" + ''.join(f"{sha(n)}  {n}\n" for n in ('MODI-Setup.bat', 'MODI-Setup.command')) + "```\n"
        else:
            assets.append({'name': 'SHA256SUMS', 'size': 200, 'browser_download_url': f'{base}/SHA256SUMS'})
    return web.json_response({'tag_name': tag if mode != 'older' else 'v0.9.0', 'name': f'MODI {tag}', 'body': body, 'html_url': f'http://127.0.0.1:{port}/rel',
                              'prerelease': False, 'draft': False, 'published_at': '2026-11-20T00:00:00Z', 'assets': assets},
                             headers={'ETag': et})
async def dl(request):
    n = request.match_info['n']
    if n == 'SHA256SUMS':
        t = ''.join(f"{sha(x)}  {x}\n" for x in ('MODI-Setup.bat', 'MODI-Setup.command'))
        if mode == 'badsum': t = t.replace(t[:4], '0000', 1) if t.startswith(sha('MODI-Setup.bat')) else t
        if mode == 'badsum': t = '\n'.join('0' * 64 + '  ' + l.split('  ')[1] for l in t.strip().split('\n')) + '\n'
        return web.Response(text=t)
    return web.FileResponse(os.path.join(adir, n))
async def stats(request): return web.json_response(hits)
app = web.Application()
app.add_routes([web.get('/repos/{o}/{r}/releases/latest', latest), web.get('/dl/{n}', dl), web.get('/stats', stats)])
web.run_app(app, host='127.0.0.1', port=port, print=None)
