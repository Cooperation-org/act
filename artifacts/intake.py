"""Turning one source item into an Artifact. Every spout (upload, email, Drive,
LinkedClaims, link) ends here, so dedupe and kind detection live in one place."""
import hashlib
import io
import logging
import mimetypes
from datetime import datetime

from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Artifact, ArtifactKind

logger = logging.getLogger(__name__)

EXIF_DATETIME_ORIGINAL = 36867
EXIF_DATETIME = 306
EXIF_IFD = 0x8769


def kind_for(content_type: str, filename: str = "") -> str:
    ct = (content_type or mimetypes.guess_type(filename)[0] or "").lower()
    if ct.startswith("image/"):
        return ArtifactKind.PHOTO
    if ct.startswith("video/"):
        return ArtifactKind.VIDEO
    if ct.startswith("audio/"):
        return ArtifactKind.VOICE
    if ct.startswith("text/plain"):
        return ArtifactKind.TEXT
    return ArtifactKind.FILE


def photo_taken_at(data: bytes):
    """When the photo was taken, from its EXIF, or None. Location tags are never read."""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            exif = im.getexif()
            raw = exif.get_ifd(EXIF_IFD).get(EXIF_DATETIME_ORIGINAL) or exif.get(EXIF_DATETIME)
        if not raw:
            return None
        return timezone.make_aware(datetime.strptime(str(raw).strip(), "%Y:%m:%d %H:%M:%S"))
    except Exception:  # not an image Pillow reads, or no usable date
        return None


def _existing(org, sha):
    return Artifact.objects.filter(org=org, sha256=sha).first()


def add_file(org, *, data: bytes, filename: str, content_type: str = "", source=None, words="",
             sent_by="", added_by=None, happened_at=None, source_ref="", source_url="", title=""):
    """Store one file as an Artifact. Returns (artifact, created). A file already
    in this org (same bytes) returns the existing record, words appended if new."""
    sha = hashlib.sha256(data).hexdigest()
    found = _existing(org, sha)
    if found:
        if words and words not in found.words:
            found.words = f"{found.words}\n\n{words}".strip()
            found.save(update_fields=["words"])
        return found, False
    content_type = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    kind = kind_for(content_type, filename)
    if happened_at is None and kind == ArtifactKind.PHOTO:
        happened_at = photo_taken_at(data)
    a = Artifact(org=org, source=source, kind=kind, sha256=sha, filename=filename[:300],
                 content_type=content_type[:120], size=len(data), words=words, sent_by=sent_by[:300],
                 added_by=added_by, happened_at=happened_at, source_ref=source_ref[:300],
                 source_url=source_url[:1000], title=title[:300])
    try:
        with transaction.atomic():
            a.file.save(filename or "file", ContentFile(data), save=False)
            a.save()
    except IntegrityError:  # the same file landed at the same moment from another spout
        return _existing(org, sha), False
    return a, True


def add_reference(org, *, kind, url, words="", source=None, sent_by="", added_by=None, happened_at=None,
                  source_ref="", title="", claim_id=None):
    """A link, a claim, or a number: no file, identified by its URL."""
    sha = hashlib.sha256(f"{kind}:{url}".encode()).hexdigest()
    found = _existing(org, sha)
    if found:
        return found, False
    a = Artifact(org=org, source=source, kind=kind, sha256=sha, source_url=url[:1000], words=words,
                 sent_by=sent_by[:300], added_by=added_by, happened_at=happened_at,
                 source_ref=str(source_ref)[:300], title=title[:300], claim_id=claim_id)
    try:
        with transaction.atomic():
            a.save()
    except IntegrityError:
        return _existing(org, sha), False
    return a, True
