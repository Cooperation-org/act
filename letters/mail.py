"""Confirmation mail. Every attempt is written to EmailLog, success or failure,
so the admin can see who never got their link. A failure schedules a retry
(Signature.record_mail_attempt, RETRY_DELAYS); `manage.py retry_confirmations`
sends whatever is due, on a systemd timer in production."""
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse

from .models import EmailLog


def confirm_url(signature, request=None):
    path = reverse("letters:confirm", args=[signature.letter.slug, signature.token])
    if request is not None:
        return request.build_absolute_uri(path)
    return settings.PUBLIC_URL.rstrip("/") + path


def send_confirmation(signature, confirm_url):
    letter = signature.letter
    subject = f"Confirm your signature: {letter.title}"
    body = render_to_string("letters/confirm_email.txt", {
        "s": signature, "letter": letter, "confirm_url": confirm_url,
    })
    log = EmailLog(letter=letter, signature=signature, to=signature.email, subject=subject)
    if not settings.EMAIL_HOST or not settings.DEFAULT_FROM_EMAIL:
        log.error = "Outgoing mail is not configured (EMAIL_HOST / DEFAULT_FROM_EMAIL)"
    else:
        try:
            send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [signature.email], fail_silently=False)
        except Exception as e:  # noqa: BLE001 — the error text is the point
            log.error = f"{type(e).__name__}: {e}"[:2000]
    log.save()
    signature.record_mail_attempt(log.error)
    return log


def retry_due(now=None):
    """Send every confirmation whose retry is due. Returns (sent, failed)."""
    from .models import Signature
    sent = failed = 0
    for s in Signature.objects.due_for_retry(now).select_related("letter"):
        if send_confirmation(s, confirm_url(s)).error:
            failed += 1
        else:
            sent += 1
    return sent, failed
