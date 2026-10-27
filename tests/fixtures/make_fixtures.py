"""Generate SYNTHETIC receipt images for tests and demos.

These are NOT golden data and NOT real receipts. They are drawn with Pillow
so the pipeline can be exercised offline without any downloaded dataset.
Run:  python tests/fixtures/make_fixtures.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"

RECEIPTS = {
    "synthetic_001": [
        "SUNRISE BAKERY",
        "12 Example Road",
        "Date: 25/12/2018  10:42",
        "Croissant      2   3.50   7.00",
        "Coffee         1   4.20   4.20",
        "Sub Total             11.20",
        "TOTAL RM              11.20",
        "Cash                  20.00",
        "Change                 8.80",
    ],
    "synthetic_002": [
        "KEDAI KOPI CONTOH",
        "1 ES TEH            22.000",
        "2 NASI GORENG       38.000",
        "SUBTOTAL            60.000",
        "TOTAL               60.000",
        "CASH               100.000",
        "KEMBALI             40.000",
    ],
    "synthetic_003": [
        "NORTHWIND HARDWARE",
        "Invoice 7781",
        "Hammer              1   15.90",
        "Nails box           2    3.10",
        "Thank you!",
    ],
}


def font(size=26):
    try:
        return ImageFont.truetype(FONT, size)
    except OSError:
        return ImageFont.load_default()


def draw(lines, path):
    f = font()
    h = 80 + 40 * len(lines)
    img = Image.new("L", (640, h), 255)
    d = ImageDraw.Draw(img)
    lines = lines + ["-- SYNTHETIC TEST FIXTURE --"]
    for i, line in enumerate(lines):
        d.text((24, 20 + 40 * i), line, fill=0, font=f)
    img.save(path)


if __name__ == "__main__":
    out = HERE / "images"
    out.mkdir(exist_ok=True)
    for rid, lines in RECEIPTS.items():
        draw(lines, out / f"{rid}.png")
        print("wrote", out / f"{rid}.png")
