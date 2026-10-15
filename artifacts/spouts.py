"""The ways material comes in from outside: email, Google Drive, LinkedClaims.

Each reads from the original and hands items to intake. None of them writes back
to the source, and none of them deletes anything there.
"""
import email
import email.policy
import imaplib
import logging
import re
from email.utils import parseaddr, parsedate_to_datetime
from urllib.parse import quote

import requests
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .intake import add_file, add_reference
from .models import ArtifactKind, Source, SourceKind

logger = logging.getLogger(__name__)


# ---- email -----------------------------------------------------------------

_QUOTED_REPLY = re.compile(r"\n(On .+ wrote:|-{2,}\s*Original Message|>.*)\n", re.IGNORECASE)


def _body_text(msg):
    part = msg.get_body(preferencelist=("plain",))
    if part is None:
        part = msg.get_body(preferencelist=("html",))
        if part is None:
            return ""
        return re.sub(r"<[^>]+>", " ", part.get_content()).strip()
    return part.get_content().strip()


def parse_email(raw: bytes):
    """One email → dict: message_id, sender, date, subject, words, files [(name, type, bytes)].
    Inline images (phone mail apps put photos inline) count as files."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    files = []
    for part in msg.iter_attachments():
        data = part.get_payload(decode=True)
        if not data:
            continue
        files.append((part.get_filename() or "attachment", part.get_content_type(), data))
    # Inline images that iter_attachments does not list (Content-Disposition: inline)
    for part in msg.walk():
        if part.get_content_maintype() in ("image", "video", "audio") and part.get_filename() is None:
            data = part.get_payload(decode=True)
            if data and not any(d == data for _, _, d in files):
                ext = part.get_content_subtype()
                files.append((f"inline.{ext}", part.get_content_type(), data))
    try:
        sent = parsedate_to_datetime(msg["Date"]) if msg["Date"] else None
    except (TypeError, ValueError):
        sent = None
    return {
        "message_id": (msg["Message-ID"] or "").strip(),
        "sender": parseaddr(msg["From"] or "")[1].lower(),
        "date": sent,
        "subject": (msg["Subject"] or "").strip(),
        "words": _body_text(msg),
        "files": files,
    }


def file_email(source, parsed):
    """Make artifacts from one parsed email. The body and subject go with every file;
    an email with no files becomes one text artifact."""
    org = source.org
    words = parsed["words"]
    if parsed["subject"]:
        words = f"{parsed['subject']}\n\n{words}".strip()
    made = []
    for name, ctype, data in parsed["files"]:
        a, created = add_file(org, data=data, filename=name, content_type=ctype, source=source, words=words,
                              sent_by=parsed["sender"], source_ref=parsed["message_id"],
                              happened_at=None if ctype.startswith("image/") else parsed["date"])
        if a.happened_at is None and parsed["date"]:
            a.happened_at = parsed["date"]
            a.save(update_fields=["happened_at"])
        made.append((a, created))
    if not parsed["files"] and words:
        a, created = add_file(org, data=words.encode(), filename="email.txt", content_type="text/plain",
                              source=source, words=words, sent_by=parsed["sender"],
                              source_ref=parsed["message_id"], happened_at=parsed["date"],
                              title=parsed["subject"])
        made.append((a, created))
    return made


def email_source_for(address):
    """The email Source whose configured address is in To/Cc/Delivered-To."""
    address = (address or "").lower()
    for s in Source.objects.filter(kind=SourceKind.EMAIL).select_related("org"):
        if (s.config.get("address") or "").lower() == address:
            return s
    return None


def poll_mailbox(host, user, password, port=993, limit=200):
    """Read unseen mail from one IMAP mailbox (Migadu) and file each message under the
    org whose intake address it was sent to. Returns (filed, skipped). Messages stay in
    the mailbox, marked read."""
    filed = skipped = 0
    box = imaplib.IMAP4_SSL(host, port)
    try:
        box.login(user, password)
        box.select("INBOX")
        _, ids = box.search(None, "UNSEEN")
        for num in ids[0].split()[:limit]:
            _, data = box.fetch(num, "(RFC822)")
            raw = data[0][1]
            msg = email.message_from_bytes(raw, policy=email.policy.default)
            recipients = [parseaddr(v)[1] for h in ("Delivered-To", "To", "Cc", "X-Original-To")
                          for v in (msg.get_all(h) or [])]
            source = next((s for s in map(email_source_for, recipients) if s), None)
            if source is None:
                skipped += 1
                logger.warning("intake mail: no org for %s", recipients)
                continue
            file_email(source, parse_email(raw))
            source.last_checked = timezone.now()
            source.last_error = ""
            source.save(update_fields=["last_checked", "last_error"])
            box.store(num, "+FLAGS", "\\Seen")
            filed += 1
    finally:
        try:
            box.logout()
        except Exception:
            pass
    return filed, skipped


# ---- Google Drive ------------------------------------------------------------

_FOLDER_ID = re.compile(r"(?:folders/|id=)([A-Za-z0-9_-]{10,})")
DRIVE_API = "https://www.googleapis.com/drive/v3/files"


def drive_folder_id(url_or_id):
    m = _FOLDER_ID.search(url_or_id or "")
    if m:
        return m.group(1)
    s = (url_or_id or "").strip()
    return s if re.fullmatch(r"[A-Za-z0-9_-]{10,}", s) else ""


def drive_enabled():
    return bool(settings.ACT_GOOGLE_API_KEY)


def import_drive(source, limit=200, max_bytes=200 * 1024 * 1024):
    """Copy every file in a Drive folder shared 'anyone with the link'. Read-only.
    Returns (new, already). Raises RuntimeError with a plain message on failure."""
    if not drive_enabled():
        raise RuntimeError("Drive import is not switched on yet.")
    folder = source.config.get("folder_id", "")
    key = settings.ACT_GOOGLE_API_KEY
    params = {"q": f"'{folder}' in parents and trashed = false", "key": key, "pageSize": 100,
              "fields": "nextPageToken, files(id, name, mimeType, size, createdTime, webViewLink, "
                        "imageMediaMetadata(time), owners(displayName, emailAddress))"}
    new = already = 0
    seen = 0
    while True:
        r = requests.get(DRIVE_API, params=params, timeout=30)
        if r.status_code != 200:
            raise RuntimeError("Google Drive would not list that folder. Check it is shared "
                               "as 'Anyone with the link'.")
        body = r.json()
        for f in body.get("files", []):
            if f["mimeType"].startswith("application/vnd.google-apps"):
                continue  # Docs/Sheets/folders need export, not copy
            if int(f.get("size") or 0) > max_bytes:
                continue
            seen += 1
            if seen > limit:
                break
            got = requests.get(f"{DRIVE_API}/{f['id']}", params={"alt": "media", "key": key}, timeout=120)
            if got.status_code != 200:
                continue
            taken = (f.get("imageMediaMetadata") or {}).get("time")
            when = None
            if taken:
                try:
                    from datetime import datetime
                    when = timezone.make_aware(datetime.strptime(taken, "%Y:%m:%d %H:%M:%S"))
                except ValueError:
                    when = None
            owner = (f.get("owners") or [{}])[0]
            _, created = add_file(source.org, data=got.content, filename=f["name"], content_type=f["mimeType"],
                                  source=source, source_ref=f["id"], source_url=f.get("webViewLink", ""),
                                  sent_by=owner.get("emailAddress") or owner.get("displayName") or "",
                                  happened_at=when or parse_datetime(f.get("createdTime") or ""))
            new += created
            already += not created
        token = body.get("nextPageToken")
        if not token or seen > limit:
            break
        params["pageToken"] = token
    return new, already


# ---- LinkedClaims ------------------------------------------------------------

def import_claims(source, limit=500):
    """Copy the LinkedClaims whose subject is this source's URL. Returns (new, already)."""
    subject = source.config.get("subject", "")
    url = f"{settings.LT_API}/api/claims/subject/{quote(subject, safe=':/')}"
    new = already = 0
    page = 1
    while True:
        r = requests.get(url, params={"page": page, "limit": 50}, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"LinkedTrust answered {r.status_code} for {subject}")
        body = r.json()
        for c in body.get("claims", []):
            words = c.get("statement") or ""
            _, created = add_reference(
                source.org, kind=ArtifactKind.CLAIM, url=f"{settings.LT_API}/claims/{c['id']}", words=words,
                source=source, sent_by=c.get("sourceURI") or c.get("issuerId") or "",
                happened_at=parse_datetime(c.get("effectiveDate") or ""), source_ref=c["id"],
                title=c.get("claim") or "", claim_id=c["id"])
            new += created
            already += not created
        pages = (body.get("pagination") or {}).get("totalPages") or 1
        if page >= pages or new + already >= limit:
            break
        page += 1
    return new, already


def check(source):
    """Pull from one source now; records the result on the source. Returns a plain sentence."""
    try:
        if source.kind == SourceKind.DRIVE:
            new, already = import_drive(source)
        elif source.kind == SourceKind.LINKEDCLAIMS:
            new, already = import_claims(source)
        else:
            return ""
        source.last_error = ""
        msg = f"{new} new" + (f", {already} already here" if already else "")
    except (RuntimeError, requests.RequestException) as e:
        source.last_error = str(e)[:500]
        msg = source.last_error
    source.last_checked = timezone.now()
    source.save(update_fields=["last_checked", "last_error"])
    return msg
