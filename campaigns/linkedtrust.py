"""Sign a testimonial as a LinkedClaim on live.linkedtrust.us.

The pattern is workers.vc's (doorway/claims.py): the browser only uploads the video
(<linked-video-recorder> → POST /api/video/upload → videoUrl); the claim itself is
written server-side with the site's client credentials so LinkedTrust stamps the issuer.
A claim written without credentials has a NULL issuer and can never be edited again, so
this module refuses to post when they are missing.

Claim shape: subject = the campaign page (what is vouched for), claim = ENDORSES,
statement = the person's words, howKnown FIRST_HAND, videoUrl when recorded, name only
when the person opted in to being shown.
"""
import logging
from datetime import date

import requests
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

VERB = "ENDORSES"


def configured():
    return bool(settings.LT_CLIENT_ID and settings.LT_CLIENT_SECRET)


def claim_url(claim_id):
    return f"{settings.LT_API}/claims/{claim_id}"


def sign_testimonial(t):
    """POST the claim; on success store claim_id/URI on the testimonial.
    Returns (ok, message). Never raises: the admin shows the message."""
    if t.claim_id:
        return True, f"already signed as claim {t.claim_id}"
    if not configured():
        t.sign_error = "LT_CLIENT_ID / LT_CLIENT_SECRET not set"
        t.save(update_fields=["sign_error"])
        return False, t.sign_error
    campaign_url = f"{settings.PUBLIC_URL}/c/{t.campaign.slug}/"
    payload = {
        "subject": campaign_url,
        "claim": VERB,
        "statement": t.quote,
        "aspect": t.relationship or "",
        "howKnown": "FIRST_HAND",
        "sourceURI": campaign_url,
        "effectiveDate": (t.created.date() if t.created else date.today()).isoformat(),
        "confidence": 1.0,
    }
    if t.video_src:
        payload["videoUrl"] = t.video_src
    if t.show_identity and t.display_name:
        payload["name"] = t.display_name
    headers = {"x-lt-client-id": settings.LT_CLIENT_ID, "x-lt-client-secret": settings.LT_CLIENT_SECRET}
    try:
        r = requests.post(f"{settings.LT_API}/api/claims", json=payload, headers=headers, timeout=30)
        if not r.ok:
            t.sign_error = f"{r.status_code}: {r.text[:200]}"
            t.save(update_fields=["sign_error"])
            logger.error("linkedtrust: claim POST -> %s", t.sign_error)
            return False, t.sign_error
        body = r.json()
        cid = body.get("id") or (body.get("claim") or {}).get("id")
        if not cid:
            t.sign_error = f"no claim id in response: {r.text[:200]}"
            t.save(update_fields=["sign_error"])
            return False, t.sign_error
    except (requests.RequestException, ValueError) as e:
        t.sign_error = str(e)[:300]
        t.save(update_fields=["sign_error"])
        logger.exception("linkedtrust: claim POST failed")
        return False, t.sign_error
    t.claim_id = int(cid)
    t.linkedclaim_uri = claim_url(cid)
    t.signed_at = timezone.now()
    t.sign_error = ""
    t.save(update_fields=["claim_id", "linkedclaim_uri", "signed_at", "sign_error"])
    return True, f"signed as claim {cid}"
