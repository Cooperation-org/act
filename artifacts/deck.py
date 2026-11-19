"""Pictures out of a slide deck. A PowerPoint file (.pptx) is a zip that keeps every
picture as its own file; a Google Slides deck shared by link is exported as one. Each
picture placed on a slide becomes its own photo in the library, titled with its slide
and the deck, in slide order. Pictures only on the master or layouts (logos, backgrounds)
are left out, as are very small ones (icons, bullets)."""
import io
import logging
import posixpath
import re
import zipfile
from xml.etree import ElementTree

logger = logging.getLogger(__name__)

DECK_TYPES = {"application/vnd.openxmlformats-officedocument.presentationml.presentation",
              "application/vnd.openxmlformats-officedocument.presentationml.slideshow"}
DECK_EXTENSIONS = (".pptx", ".ppsx")
PICTURE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
SMALLEST_SIDE = 200   # px on the longer side; smaller is an icon or a bullet
MAX_PICTURES = 300

R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
SLIDES_LINK = re.compile(r"https://docs\.google\.com/presentation/d/([A-Za-z0-9_-]{20,})")


def is_deck(filename, content_type=""):
    return (content_type or "").split(";")[0].strip().lower() in DECK_TYPES or \
        (filename or "").lower().endswith(DECK_EXTENSIONS)


def _rels(z, part):
    """{relationship id: target part path} for one part of the package."""
    folder, name = posixpath.split(part)
    path = posixpath.join(folder, "_rels", name + ".rels")
    if path not in z.namelist():
        return {}
    out = {}
    for r in ElementTree.fromstring(z.read(path)).iter(f"{{{REL_NS}}}Relationship"):
        if r.get("TargetMode") == "External":
            continue
        out[r.get("Id")] = posixpath.normpath(posixpath.join(folder, r.get("Target", "")))
    return out


def pictures(data):
    """[(slide number, filename, bytes)] in slide order, each picture once (where it first appears)."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        pres_rels = _rels(z, "ppt/presentation.xml")
        pres = ElementTree.fromstring(z.read("ppt/presentation.xml"))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError):
        return []
    slides = [pres_rels.get(s.get(f"{{{R_NS}}}id")) for s in pres.iter(f"{{{P_NS}}}sldId")]
    seen, out = set(), []
    for n, slide in enumerate((s for s in slides if s), start=1):
        for target in _rels(z, slide).values():
            ext = posixpath.splitext(target)[1].lower()
            if target in seen or ext not in PICTURE_EXTENSIONS or target not in z.namelist():
                continue
            seen.add(target)
            body = z.read(target)
            if _too_small(body):
                continue
            out.append((n, posixpath.basename(target), body))
            if len(out) >= MAX_PICTURES:
                return out
    return out


def _too_small(body):
    try:
        from PIL import Image
        with Image.open(io.BytesIO(body)) as im:
            return max(im.size) < SMALLEST_SIDE
    except Exception:
        return False   # not readable here; add_file decides


def add_pictures(org, deck, data):
    """Each picture in the deck as its own photo. Returns how many were new."""
    from .intake import BrokenFile, add_file
    name = deck.title or posixpath.splitext(deck.filename or "")[0] or "the deck"
    new = 0
    for n, filename, body in pictures(data):
        try:
            _, created = add_file(org, data=body, filename=filename, source=deck.source, sent_by=deck.sent_by,
                                  added_by=deck.added_by, happened_at=deck.happened_at,
                                  source_url=deck.source_url, title=f"Slide {n} of {name}")
        except BrokenFile:
            continue
        new += created
    return new


def slides_id(url):
    m = SLIDES_LINK.match(url or "")
    return m.group(1) if m else None


def copy_slides_link(org, url, **who):
    """Download a Google Slides deck shared by link as .pptx, keep it, and add its pictures.
    Returns the deck artifact, or None when Google does not hand it over (not shared by link)."""
    import requests
    from .intake import add_file
    deck_id = slides_id(url)
    if not deck_id:
        return None
    r = requests.get(f"https://docs.google.com/presentation/d/{deck_id}/export/pptx", timeout=300)
    if r.status_code != 200 or not r.content.startswith(b"PK"):
        return None
    from urllib.parse import unquote
    said = r.headers.get("content-disposition", "")
    m = re.search(r"filename\*=UTF-8''([^;]+)", said) or re.search(r'filename="([^"]+)"', said)
    filename = unquote(m.group(1)).strip() if m else ""
    filename = filename or f"slides-{deck_id}.pptx"
    deck, _ = add_file(org, data=r.content, filename=filename, content_type=next(iter(DECK_TYPES)),
                       source_url=url, **who)
    return deck


def copy_slides_in_background(org, url, **who):
    import threading

    def run():
        from django.db import connection
        try:
            if copy_slides_link(org, url, **who) is None:
                logger.info("slides link %s for org %s not shared by link", url, org.pk)
        except Exception:
            logger.exception("copying slides %s for org %s failed", url, org.pk)
        finally:
            connection.close()
    threading.Thread(target=run, daemon=True).start()
