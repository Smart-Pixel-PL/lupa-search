from PIL import Image, ImageDraw, ImageFilter
S = 1024
def icon():
    # gradient background
    g = Image.new("RGBA", (S, S))
    top, bot = (98, 110, 255), (143, 72, 230)
    px = g.load()
    for y in range(S):
        for x in range(S):
            t = (x * 0.35 + y * 0.65) / S
            px[x, y] = tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)) + (255,)
    # macOS squircle-ish mask with margin (Big Sur grid: 824px body on 1024 canvas)
    m = Image.new("L", (S, S), 0)
    pad = 100
    ImageDraw.Draw(m).rounded_rectangle([pad, pad, S - pad, S - pad], radius=185, fill=255)
    body = Image.new("RGBA", (S, S), (0, 0, 0, 0)); body.paste(g, (0, 0), m)
    # soft shadow
    sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([pad, pad + 14, S - pad, S - pad + 14], radius=185, fill=(0, 0, 0, 90))
    sh = sh.filter(ImageFilter.GaussianBlur(18))
    out = Image.alpha_composite(sh, body)
    # glossy top highlight
    hl = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    hd = ImageDraw.Draw(hl)
    for y in range(pad, S // 2 + 80):
        a = int(34 * max(0, 1 - (y - pad) / (S // 2 + 80 - pad)) ** 1.6)
        hd.line([(0, y), (S, y)], fill=(255, 255, 255, a))
    hl.putalpha(Image.composite(hl.getchannel("A"), Image.new("L", (S, S), 0), m))
    out = Image.alpha_composite(out, hl)
    # magnifier (drawn at 2x then downsampled for smooth edges)
    L = Image.new("RGBA", (S * 2, S * 2), (0, 0, 0, 0)); d = ImageDraw.Draw(L)
    cx, cy, r, w = 900, 880, 300, 92
    d.line([(cx + 215, cy + 215), (1520, 1520)], fill=(255, 255, 255, 255), width=150)
    d.ellipse([1520 - 75, 1520 - 75, 1520 + 75, 1520 + 75], fill=(255, 255, 255, 255))
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 255, 255, 255), width=w)
    d.ellipse([cx - r + w, cy - r + w, cx + r - w, cy + r - w], fill=(255, 255, 255, 60))
    # little image glyph inside the lens (mountain + sun) — "visual search"
    d.polygon([(cx - 150, cy + 110), (cx - 40, cy - 20), (cx + 30, cy + 60), (cx + 80, cy + 10), (cx + 160, cy + 110)], fill=(255, 255, 255, 235))
    d.ellipse([cx + 40, cy - 140, cx + 120, cy - 60], fill=(255, 214, 90, 255))
    L = L.resize((S, S), Image.LANCZOS)
    return Image.alpha_composite(out, L)
im = icon()
im.save("icon_1024.png")
import os
os.makedirs("Lupa.iconset", exist_ok=True)
for s in (16, 32, 128, 256, 512):
    im.resize((s, s), Image.LANCZOS).save(f"Lupa.iconset/icon_{s}x{s}.png")
    im.resize((s * 2, s * 2), Image.LANCZOS).save(f"Lupa.iconset/icon_{s}x{s}@2x.png")
