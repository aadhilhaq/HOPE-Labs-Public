"""Draw the README banner: the HOPE Labs mark on a panel, the name, and what it is for.

By Aadhil Haq

One banner serves both editions, so its words are true of both. Committed as a script and not
only as a PNG, so it can be redrawn when they change:

    python docs/brand/make_banner.py        # rewrites docs/brand/banner.png
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from make_icon import CLEAR, PAPER, RUST, draw_mark                # noqa: E402
# The name comes from the package, not a second copy here.
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from hope_labs import APP                                          # noqa: E402

DEEP = "#113f4a"
DUSK = "#0b2e37"
MUTED = "#9fc4c4"
GLOW = "#1a8a8a"                                # the motif's teal: the mark's own is lost on DEEP
ATTRIBUTION = "by the HOPE Lab, Texas A&M University"
LINE = "Design, fold and simulate on a Slurm cluster, from one sign-in."

LAYOUT = dict(W=1280, H=340, title=58, body=21, small=19, x=248,
              y_title=96, y_tag=176, y_rule=252, y_small=274,
              rule=(96, 4), plate=156, plate_x=64, glyph=132, radius=34,
              tile=26, tile_gap=12, motif_pad=48, motif_gap=72, name="banner.png")


def _font(name, size):
    """DejaVu, from matplotlib's copy or the system, whichever is present."""
    here = []
    try:
        import matplotlib
        here.append(os.path.join(os.path.dirname(matplotlib.__file__), "mpl-data", "fonts", "ttf"))
    except Exception:                                               # noqa: BLE001
        pass
    here += ["/usr/share/fonts/dejavu", "/usr/share/fonts/truetype/dejavu", "/Library/Fonts",
             "C:\\Windows\\Fonts"]
    for folder in here:
        path = os.path.join(folder, name)
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    # A banner is a nicety: the bitmap font gives something plain rather than failing.
    return ImageFont.load_default()


def _rgb(colour):
    if isinstance(colour, str):
        return tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return tuple(colour[:3])


def _mix(a, b, t):
    a, b = _rgb(a), _rgb(b)
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def banner(path=None, L=LAYOUT):
    W, H = L["W"], L["H"]
    image = Image.new("RGB", (W, H), DEEP)
    pen = ImageDraw.Draw(image)

    # A wash across the width rather than a flat fill: the mark sits on the darkest part.
    for x in range(W):
        pen.line([(x, 0), (x, H)], fill=_mix(DEEP, DUSK, x / float(W)))

    f_title = _font("DejaVuSans-Bold.ttf", L["title"])
    f_body = _font("DejaVuSans.ttf", L["body"])
    f_small = _font("DejaVuSans.ttf", L["small"])
    x = L["x"]
    lines = [(L["y_title"], APP, f_title, _rgb(PAPER)),
             (L["y_tag"], ATTRIBUTION, f_body, _rgb(MUTED)),
             (L["y_small"], LINE, f_small, _rgb(MUTED))]

    # The motif: the mark's tiles again, two rows of them fading into the ground, one rust. It
    # starts where the longest line ends, so it never runs through a sentence when the words
    # change, and is left out when there is no room for it.
    end = max(x + pen.textlength(text, font=font) for _y, text, font, _c in lines[1:])
    start = int(end) + L["motif_gap"]
    tile, gap = L["tile"], L["tile_gap"]
    cols = int((W - L["motif_pad"] - start + gap) // (tile + gap))
    if cols >= 3:
        top = L["y_small"] + L["small"] - (2 * tile + gap)
        left = W - L["motif_pad"] - cols * (tile + gap) + gap
        for col in range(cols):
            fade = 0.2 + 0.4 * (col + 1) / cols
            for row in range(2):
                rust = (row, col) == (1, cols - 2)
                fill = _mix(DUSK, RUST if rust else GLOW, 0.85 if rust else fade)
                tx, ty = left + col * (tile + gap), top + row * (tile + gap)
                pen.rounded_rectangle([tx, ty, tx + tile, ty + tile], radius=tile * 0.26, fill=fill)

    # The mark on a paper panel, its own ground in the launcher's icon, so its teal and rust
    # are the colors the launcher shows rather than ones adjusted for a dark ground.
    plate, px0, glyph = L["plate"], L["plate_x"], L["glyph"]
    py0 = (H - plate) // 2
    pen.rounded_rectangle([px0, py0, px0 + plate, py0 + plate], radius=L["radius"],
                          fill=_rgb(PAPER))
    mark = draw_mark(glyph, background=CLEAR)
    image.paste(mark, (px0 + (plate - glyph) // 2, py0 + (plate - glyph) // 2), mark)

    for y, text, font, colour in lines:
        pen.text((x, y), text, font=font, fill=colour)

    # A short warm rule under the name: an accent, not a divider.
    rule_w, rule_h = L["rule"]
    pen.rectangle([x, L["y_rule"], x + rule_w, L["y_rule"] + rule_h], fill=_rgb(RUST))

    path = path or os.path.join(HERE, L["name"])
    image.save(path, optimize=True)
    return path


if __name__ == "__main__":
    print(banner())
