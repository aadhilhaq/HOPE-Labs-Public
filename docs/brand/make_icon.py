"""Draw the launcher's icon: the HOPE Labs mark, four tiles with the last one rust.

Run at build time, so no image file has to be kept in the repository:

    python docs/brand/make_icon.py          # writes docs/brand/hopelabs.ico and .png

Pillow is a build-time dependency only; the launcher draws its own header on a Tk canvas and the
page draws its mark in SVG, so neither needs an image at all.
"""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
TEAL = (13, 92, 107, 255)
RUST = (194, 96, 58, 255)
PAPER = (244, 248, 248, 255)
CLEAR = (0, 0, 0, 0)
SIZES = (16, 32, 48, 64, 128, 256)


def draw_mark(size=256, background=PAPER):
    """The mark at one size: four tiles, as a launcher's grid, with one of them the odd one out.

    A grid rather than a molecule: this is the front for several tools, and each tool already has
    a mark of its own.
    """
    scale = 4                                   # drawn large and reduced: Pillow has no antialiasing
    big = size * scale
    image = Image.new("RGBA", (big, big), background)
    pen = ImageDraw.Draw(image)
    tile = big * 0.30
    gap = big * 0.08
    left = (big - (2 * tile + gap)) / 2.0
    radius = tile * 0.26
    for row in range(2):
        for col in range(2):
            x = left + col * (tile + gap)
            y = left + row * (tile + gap)
            colour = RUST if (row, col) == (1, 1) else TEAL
            pen.rounded_rectangle([x, y, x + tile, y + tile], radius=radius, fill=colour)
    return image.resize((size, size), Image.LANCZOS)


def main():
    # The .exe's icon on a clear ground, as the window draws its own (app.py, mark_image): on paper
    # the taskbar and Explorer showed a pale square around the tiles. The PNG keeps its paper ground,
    # for pages that show it on white.
    icons = [draw_mark(s, background=CLEAR) for s in SIZES]
    ico = os.path.join(HERE, "hopelabs.ico")
    icons[-1].save(ico, sizes=[(s, s) for s in SIZES])
    png = os.path.join(HERE, "hopelabs.png")
    draw_mark(256).save(png)
    print("wrote %s and %s" % (ico, png))


if __name__ == "__main__":
    main()
