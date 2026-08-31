"""Share-card PNG for channels with no web share intent (Instagram bio/story)."""
import textwrap
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

TEAL = (37, 100, 106)
BG = (252, 252, 251)
INK = (26, 26, 26)
FADED = (118, 118, 118)

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
FONT_SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

SIZES = {"story": (1080, 1920), "post": (1080, 1350)}


def render_card(campaign, url, kind="story"):
    w, h = SIZES.get(kind, SIZES["story"])
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    title_f = ImageFont.truetype(FONT, 76)
    body_f = ImageFont.truetype(FONT_SANS, 42)
    small_f = ImageFont.truetype(FONT_SANS, 34)

    d.rectangle([0, 0, w, 18], fill=TEAL)
    y = h * 0.16
    for line in textwrap.wrap(campaign.title, width=22):
        d.text((80, y), line, font=title_f, fill=INK)
        y += 96
    y += 30
    for line in textwrap.wrap(campaign.summary or "", width=44)[:6]:
        d.text((80, y), line, font=body_f, fill=INK)
        y += 58
    d.text((80, h - 200), campaign.org.name, font=small_f, fill=FADED)
    d.text((80, h - 150), url, font=small_f, fill=TEAL)
    d.rectangle([0, h - 18, w, h], fill=TEAL)

    buf = BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
