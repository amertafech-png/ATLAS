"""Generate MODI icons (png, ico, icns) from one 1024px master. Needs Pillow."""
import math, os, sys
from PIL import Image, ImageDraw, ImageFilter, ImageFont
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'assets')
S = 1024
def master():
    im = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    # rounded tile with radial gradient
    bg = Image.new('RGBA', (S, S))
    px = bg.load()
    for y in range(S):
        for x in range(S):
            d = math.hypot(x - S/2, y - S/2) / (S*0.72)
            t = min(1, d)
            px[x, y] = (int(8 + 4*t*0), int(36 - 24*t), int(62 - 34*t), 255)
    mask = Image.new('L', (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((40, 40, S-40, S-40), radius=210, fill=255)
    im.paste(bg, (0, 0), mask)
    cyan = (40, 220, 255)
    glow = Image.new('RGBA', (S, S), (0, 0, 0, 0)); g = ImageDraw.Draw(glow)
    c = S/2
    def ring(r, w, col=cyan, a=255):
        g.ellipse((c-r, c-r, c+r, c+r), outline=col + (a,), width=w)
    ring(430, 10, a=200); ring(385, 6, a=120)
    # tick marks
    for i in range(72):
        ang = math.radians(i*5); r1 = 452 if i % 6 == 0 else 462; r2 = 478
        g.line((c+r1*math.cos(ang), c+r1*math.sin(ang), c+r2*math.cos(ang), c+r2*math.sin(ang)), fill=cyan+(150,), width=6)
    # arc segments (reactor ring)
    for i in range(10):
        a0 = i*36 + 4
        g.arc((c-330, c-330, c+330, c+330), a0, a0+26, fill=cyan+(255,), width=64)
    ring(250, 8, a=230); ring(215, 5, a=140)
    # core glow
    core = Image.new('RGBA', (S, S), (0, 0, 0, 0)); cd = ImageDraw.Draw(core)
    for r in range(200, 0, -4):
        a = int(220 * (1 - r/200) ** 1.4)
        cd.ellipse((c-r, c-r, c+r, c+r), fill=(120, 235, 255, a))
    blur = glow.filter(ImageFilter.GaussianBlur(14))
    im = Image.alpha_composite(im, blur); im = Image.alpha_composite(im, glow); im = Image.alpha_composite(im, core)
    # border
    ImageDraw.Draw(im).rounded_rectangle((40, 40, S-40, S-40), radius=210, outline=(60, 200, 235, 160), width=8)
    # M
    d = ImageDraw.Draw(im)
    font = None
    for p in ('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf'):
        if os.path.exists(p): font = ImageFont.truetype(p, 250); break
    font = font or ImageFont.load_default()
    d.text((c, c+6), 'M', font=font, fill=(6, 26, 44, 255), anchor='mm')
    return im
m = master()
os.makedirs(OUT, exist_ok=True)
m.resize((256, 256), Image.LANCZOS).save(os.path.join(OUT, 'modi.png'))
m.save(os.path.join(OUT, 'modi.ico'), sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
m.save(os.path.join(OUT, 'modi.icns'), sizes=[(16,16),(32,32),(64,64),(128,128),(256,256),(512,512),(1024,1024)])
m.save('/tmp/modi-master.png')
print('icons written')
