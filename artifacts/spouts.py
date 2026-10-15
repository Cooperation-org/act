"""The ways material comes in from outside: email, Google Drive, LinkedClaims.

Each reads from the original and hands items to intake. None of them writes back
to the source, and none of them deletes anything there.
"""
import email
import email.policy
import html
import imaplib
import logging
import mimetypes
import re
import threading
from datetime import datetime
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
    """Drive import always works for folders shared 'Anyone with the link'. With
    ACT_GOOGLE_API_KEY it uses the Drive API (dates, owners); without, Drive's public
    folder view, which gives names and files only."""
    return True


def _drive_api_files(folder, key):
    params = {"q": f"'{folder}' in parents and trashed = false", "key": key, "pageSize": 100,
              "fields": "nextPageToken, files(id, name, mimeType, size, createdTime, webViewLink, "
                        "imageMediaMetadata(time), owners(displayName, emailAddress))"}
    while True:
        r = requests.get(DRIVE_API, params=params, timeout=30)
        if r.status_code != 200:
            raise RuntimeError("Google Drive would not list that folder. Check it is shared "
                               "as 'Anyone with the link'.")
        body = r.json()
        for f in body.get("files", []):
            when = None
            taken = (f.get("imageMediaMetadata") or {}).get("time")
            if taken:
                try:
                    when = timezone.make_aware(datetime.strptime(taken, "%Y:%m:%d %H:%M:%S"))
                except ValueError:
                    when = None
            owner = (f.get("owners") or [{}])[0]
            yield {"id": f["id"], "name": f["name"], "mime": f["mimeType"], "size": int(f.get("size") or 0),
                   "url": f.get("webViewLink", ""), "when": when or parse_datetime(f.get("createdTime") or ""),
                   "by": owner.get("emailAddress") or owner.get("displayName") or ""}
        if not body.get("nextPageToken"):
            return
        params["pageToken"] = body["nextPageToken"]


_EMBED_ENTRY = re.compile(
    r'<a href="https://drive\.google\.com/(file/d|drive/folders)/([A-Za-z0-9_-]+)[^"]*".*?'
    r'(?:/type/([a-z]+/[A-Za-z0-9.+-]+))?".*?<div class="flip-entry-title">([^<]*)</div>', re.S)


def _drive_public_files(folder, depth=0, max_depth=3):
    """Files in a folder shared 'Anyone with the link', from Drive's public folder view
    (no key needed). Walks subfolders a few levels down. Google Docs/Sheets are skipped:
    they are not files to copy."""
    r = requests.get("https://drive.google.com/embeddedfolderview", params={"id": folder}, timeout=30)
    if r.status_code != 200 or "flip-entry" not in r.text and "flip-list" not in r.text:
        raise RuntimeError("Google Drive would not show that folder. Check it is shared as 'Anyone with the link'.")
    for entry in r.text.split('<div class="flip-entry"')[1:]:
        m = _EMBED_ENTRY.search(entry)
        if not m:
            continue
        what, fid, mime, name = m.groups()
        name = html.unescape(name)
        if what == "drive/folders":
            if depth < max_depth:
                yield from _drive_public_files(fid, depth + 1, max_depth)
            continue
        yield {"id": fid, "name": name, "mime": mime or mimetypes.guess_type(name)[0] or "",
               "size": 0, "url": f"https://drive.google.com/file/d/{fid}/view", "when": None, "by": ""}


def _drive_download(f, key):
    if key:
        return requests.get(f"{DRIVE_API}/{f['id']}", params={"alt": "media", "key": key}, timeout=300)
    return requests.get("https://drive.usercontent.google.com/download",
                        params={"id": f["id"], "export": "download", "confirm": "t"}, timeout=300)


def import_drive(source, limit=300, max_bytes=300 * 1024 * 1024):
    """Copy every file in a Drive folder shared 'Anyone with the link'. Read-only: the
    folder is never changed. Returns (new, already). Raises RuntimeError with a plain
    message on failure."""
    folder = source.config.get("folder_id", "")
    key = settings.ACT_GOOGLE_API_KEY
    files = _drive_api_files(folder, key) if key else _drive_public_files(folder)
    new = already = seen = 0
    have = set(source.artifacts.values_list("source_ref", flat=True))
    for f in files:
        if f["mime"].startswith("application/vnd.google-apps") or f["size"] > max_bytes:
            continue
        if f["id"] in have:
            already += 1
            continue
        seen += 1
        if seen > limit:
            break
        got = _drive_download(f, key)
        ctype = got.headers.get("Content-Type", "")
        if got.status_code != 200 or ctype.startswith("text/html") or len(got.content) > max_bytes:
            continue  # too large for Drive's direct download, or not shared
        _, created = add_file(source.org, data=got.content, filename=f["name"], content_type=f["mime"],
                              source=source, source_ref=f["id"], source_url=f["url"], sent_by=f["by"],
                              happened_at=f["when"])
        new += created
        already += not created
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


def check_in_background(source):
    """A Drive folder can take minutes to copy; do it off the request. The result is
    recorded on the source (last_checked, last_error), which the Ways page shows."""
    def run(pk):
        from django.db import connection
        try:
            check(Source.objects.get(pk=pk))
        except Exception:
            logger.exception("background check of source %s failed", pk)
        finally:
            connection.close()
    threading.Thread(target=run, args=(source.pk,), daemon=True).start()
