"""Reconcile backstop for vouches, plus the "new vouch" notification.

Vouches are made through act's own form (campaigns.views.vouch): act posts the ENDORSES
claim with its client credentials and records a local Testimonial row in the same request,
so the normal path needs no polling. This module is the safety net — `sync_vouches` walks
the LinkedTrust feed for a campaign's subject and mirrors any ENDORSES claim act does not
already have. Anything picked up this way lands PENDING (it did not come through our form,
so a human confirms it before it shows), mirroring workers.vc's rule that a claim posted
straight to the API never auto-appears on the wall.
"""
import logging

import requests
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from .models import Testimonial

logger = logging.getLogger(__name__)

VOUCH_VERB = "ENDORSES"


def subject_uri(campaign):
    """The URI a vouch is about: this campaign's public page (same value act signs with)."""
    return f"{settings.PUBLIC_URL}/c/{campaign.slug}/"


def _fetch_claims(subject):
    """Claims LinkedTrust holds about `subject`. Returns [] on any error (never raises)."""
    url = f"{settings.LT_API}/api/claims/subject/{requests.utils.quote(subject, safe='')}"
    try:
        r = requests.get(url, params={"includeLinked": "false", "limit": 500}, timeout=15)
        r.raise_for_status()
        return r.json().get("claims", []) or []
    except (requests.RequestException, ValueError) as e:
        logger.warning("vouch_sync: fetch for %s failed: %s", subject, e)
        return []


def pull_vouches(campaign):
    """Reconcile backstop: mirror ENDORSES claims about the campaign we do not have yet.

    Rows created here always land PENDING (they did not come through act's form), so a
    human confirms them before they show. Returns the list of newly created Testimonials.
    """
    subject = subject_uri(campaign)
    have = set(campaign.testimonials.exclude(claim_id__isnull=True).values_list("claim_id", flat=True))
    new = []
    for claim in _fetch_claims(subject):
        cid = claim.get("id")
        if not cid or cid in have or (claim.get("claim") or "").upper() != VOUCH_VERB:
            continue
        if (claim.get("subject") or "") != subject:  # ignore linked-subject spillover
            continue
        video = next((img.get("url") for img in (claim.get("images") or [])
                      if (img.get("type") == "video") and img.get("url")), "")
        t = Testimonial.objects.create(
            campaign=campaign,
            quote=(claim.get("statement") or "").strip(),
            relationship=(claim.get("aspect") or "").strip()[:200],
            video_url=video,
            source_uri=(claim.get("sourceURI") or "").strip()[:300],
            claim_id=cid,
            linkedclaim_uri=f"{settings.LT_API}/claims/{cid}",
            signed_at=timezone.now(),
            status="pending",
        )
        have.add(cid)
        new.append(t)
    if new:
        notify_new_vouches(campaign, new, "pending")
    return new


def notify_new_vouches(campaign, new, status):
    """Email the org's approvers that vouches arrived. No-op if mail is not configured."""
    recipients = _approver_emails(campaign)
    if not (settings.EMAIL_HOST and settings.DEFAULT_FROM_EMAIL and recipients):
        return
    where = "waiting for review" if status == "pending" else "live on the page"
    lines = [f"- {(t.quote or '(no words)')[:140]}" for t in new]
    body = (f"{len(new)} new vouch{'es' if len(new) != 1 else ''} for {campaign.title} "
            f"({where}):\n\n" + "\n".join(lines) +
            f"\n\n{settings.PUBLIC_URL}/c/{campaign.slug}/")
    try:
        send_mail(f"New vouch for {campaign.title}", body, settings.DEFAULT_FROM_EMAIL,
                  recipients, fail_silently=True)
    except Exception:
        logger.exception("vouch_sync: notification email failed")


def _approver_emails(campaign):
    users = campaign.org.volunteers.filter(is_approver=True).select_related("user")
    emails = [v.user.email for v in users if v.user.email]
    fallback = getattr(settings, "VOUCH_NOTIFY_EMAIL", "")
    if fallback and fallback not in emails:
        emails.append(fallback)
    return emails
