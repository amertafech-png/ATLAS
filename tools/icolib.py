"""ICO helpers shared by make_icons.py (writer) and build.py (validator).
Windows shortcuts need a *real* multi-size .ico: small sizes as classic 32-bit BMP (works everywhere, incl. Explorer's icon cache),
256 px as PNG (Vista+). A PNG-only or single-size .ico is what makes shortcuts show a blank white page."""
import io, struct

REQUIRED = (16, 24, 32, 48, 64, 256)
SIZES = (16, 24, 32, 48, 64, 128, 256)


def write_ico(master, path, sizes=SIZES):
    from PIL import Image
    ents = []
    for s in sizes:
        im = master.convert('RGBA').resize((s, s), Image.LANCZOS)
        if s >= 256:
            b = io.BytesIO(); im.save(b, 'PNG'); data = b.getvalue()
        else:
            px = im.load(); rows = []
            for y in range(s - 1, -1, -1):                       # DIB rows are bottom-up, BGRA
                rows.append(b''.join(bytes((px[x, y][2], px[x, y][1], px[x, y][0], px[x, y][3])) for x in range(s)))
            mask = b'\x00' * (((s + 31) // 32) * 4 * s)           # AND mask: all opaque (alpha channel does the work)
            data = struct.pack('<IiiHHIIiiII', 40, s, s * 2, 1, 32, 0, len(b''.join(rows)) + len(mask), 0, 0, 0, 0) + b''.join(rows) + mask
        ents.append((s, data))
    out = struct.pack('<HHH', 0, 1, len(ents)); off = 6 + 16 * len(ents); body = b''
    for s, data in ents:
        out += struct.pack('<BBBBHHII', s % 256, s % 256, 0, 0, 1, 32, len(data), off + len(body)); body += data
    open(path, 'wb').write(out + body)


def check_ico(path):
    """Return (ok, message). Valid header, every entry decodable with matching size, and all REQUIRED sizes present."""
    d = open(path, 'rb').read()
    if len(d) < 22: return False, 'file too small'
    r, t, n = struct.unpack('<HHH', d[:6])
    if (r, t) != (0, 1) or n < 1: return False, 'not an ICO (bad header)'
    seen = {}
    for i in range(n):
        w, h, _c, _r, planes, bpp, size, off = struct.unpack('<BBBBHHII', d[6 + 16 * i:22 + 16 * i])
        w, h = w or 256, h or 256
        blob = d[off:off + size]
        if off + size > len(d) or len(blob) != size: return False, 'entry %d runs past the end of the file' % i
        if blob[:8] == b'\x89PNG\r\n\x1a\n':
            pw, ph = struct.unpack('>II', blob[16:24])
            if (pw, ph) != (w, h): return False, 'PNG entry %d is %dx%d but declared %dx%d' % (i, pw, ph, w, h)
        else:
            hs, bw, bh = struct.unpack('<Iii', blob[:12])
            if hs != 40 or bw != w or bh != h * 2: return False, 'BMP entry %d header does not match %dx%d' % (i, w, h)
        seen[w] = 1
    miss = [s for s in REQUIRED if s not in seen]
    if miss: return False, 'missing sizes: ' + ', '.join(map(str, miss))
    return True, 'ok (%s px)' % ', '.join(str(s) for s in sorted(seen))
