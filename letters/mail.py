"""Confirmation mail. Every attempt is written to EmailLog, success or failure,
so the admin can see who never got their link."""
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone

from .models import EmailLog


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
    if not log.error:
        signature.confirm_sent_at = timezone.now()
        signature.confirm_sends += 1
        signature.save(update_fields=["confirm_sent_at", "confirm_sends"])
    return log
