"""Signable documents: open letters and petitions.

A Letter belongs to an Org. It defines which optional fields a signer is asked
for (SignatureField). A Signature counts once the signer has either confirmed
their email or drawn a signature, or an admin has ticked "approved". EmailLog
records every mail attempt so an admin can see who is stuck confirming; a
failed confirmation mail is retried with backoff (RETRY_DELAYS) by the
retry_confirmations command, and the schedule lives on the Signature.
"""
import secrets
from datetime import timedelta

from django.db import models
from django.db.models import Q
from django.utils import timezone

from campaigns.models import Org, PublishStatus

# After the n-th failed confirmation mail, wait this long before trying again.
# After the last one fails, stop; the admin can resend or approve by hand.
RETRY_DELAYS = [timedelta(hours=1), timedelta(hours=6), timedelta(hours=24)]


class Letter(models.Model):
    class Kind(models.TextChoices):
        OPEN_LETTER = "open_letter", "Open letter"
        PETITION = "petition", "Petition"

    class Theme(models.TextChoices):
        PARCHMENT = "parchment", "Parchment"

    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="letters")
    slug = models.SlugField(unique=True)
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.OPEN_LETTER)
    title = models.CharField(max_length=200)
    body = models.TextField(help_text="Markdown: **bold**, *italic*, blank line between paragraphs, - bullets")
    addressee = models.CharField(max_length=200, blank=True, help_text="Petitions: who it is addressed to")
    goal = models.PositiveIntegerField(null=True, blank=True, help_text="Petitions: signature target")
    theme = models.CharField(max_length=12, choices=Theme.choices, default=Theme.PARCHMENT)
    show_signatures = models.BooleanField(default=True)
    updates_label = models.CharField(max_length=120, default="Keep me updated about this letter")
    status = models.CharField(max_length=12, choices=PublishStatus.choices, default=PublishStatus.DRAFT)
    created = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return self.title

    @property
    def is_published(self):
        return self.status == PublishStatus.PUBLISHED

    def valid_signatures(self):
        return self.signatures.valid().filter(hidden=False)

    def count(self):
        return self.valid_signatures().count()


class SignatureField(models.Model):
    """An extra question the letter asks each signer, in the letter's own words."""

    class Kind(models.TextChoices):
        SHORT = "short", "Short text"
        LONG = "long", "Paragraph"
        CHECKBOX = "checkbox", "Checkbox"

    letter = models.ForeignKey(Letter, on_delete=models.CASCADE, related_name="fields")
    key = models.SlugField(help_text="Stored under this name, e.g. affiliation")
    label = models.CharField(max_length=120, help_text="Shown to the signer, e.g. Affiliation")
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.SHORT)
    required = models.BooleanField(default=False)
    public = models.BooleanField(default=True, help_text="Shown next to the signature on the page and PDF")
    sort = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort", "id"]
        unique_together = [("letter", "key")]

    def __str__(self):
        return f"{self.letter.slug}: {self.label}"


def _token():
    return secrets.token_urlsafe(32)


class SignatureQuerySet(models.QuerySet):
    def valid(self):
        return self.filter(Q(confirmed_at__isnull=False) | ~Q(drawn="") | Q(approved=True))

    def due_for_retry(self, now=None):
        """Unconfirmed email signatures whose next scheduled retry has come."""
        now = now or timezone.now()
        return self.exclude(email="").filter(confirmed_at__isnull=True, confirm_next_retry_at__lte=now)

    def for_pdf(self):
        return self.valid().filter(hidden=False, on_pdf=True).order_by("pdf_sort", "created")


class Signature(models.Model):
    letter = models.ForeignKey(Letter, on_delete=models.CASCADE, related_name="signatures")
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(blank=True)
    city = models.CharField(max_length=100)
    country = models.CharField(max_length=100)
    drawn = models.ImageField(upload_to="signatures/", blank=True)
    extras = models.JSONField(default=dict, blank=True, help_text="Answers to the letter's own fields, by key")
    keep_updated = models.BooleanField(default=False)
    token = models.CharField(max_length=64, unique=True, default=_token)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirm_sent_at = models.DateTimeField(null=True, blank=True)
    confirm_sends = models.PositiveSmallIntegerField(default=0)
    confirm_attempts = models.PositiveSmallIntegerField(default=0, help_text="Confirmation mails tried, sent or failed")
    confirm_last_attempt_at = models.DateTimeField(null=True, blank=True)
    confirm_last_error = models.TextField(blank=True)
    confirm_next_retry_at = models.DateTimeField(null=True, blank=True, help_text="When the next retry is due; empty = none scheduled")
    approved = models.BooleanField(default=False, help_text="Counts and is shown even without a confirmed email or drawn signature")
    hidden = models.BooleanField(default=False, help_text="Kept, never shown")
    on_pdf = models.BooleanField(default=True)
    pdf_sort = models.IntegerField(default=0, help_text="Lower comes first on the PDF")
    ip = models.GenericIPAddressField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    objects = SignatureQuerySet.as_manager()

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return f"{self.full_name} on {self.letter.slug}"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def is_valid(self):
        return bool(self.confirmed_at or self.drawn or self.approved)

    @property
    def confirmation(self):
        if self.confirmed_at:
            return "confirmed"
        if self.drawn:
            return "drawn"
        if self.approved:
            return "approved"
        if self.email and self.confirm_last_error:
            return "retrying" if self.confirm_next_retry_at else "gave up"
        if self.email and self.confirm_sent_at:
            return "waiting"
        return "unsent"

    def record_mail_attempt(self, error=""):
        """Update the confirmation schedule after one send attempt (see mail.py)."""
        now = timezone.now()
        self.confirm_attempts += 1
        self.confirm_last_attempt_at = now
        self.confirm_last_error = error
        if error:
            failures = self.confirm_attempts - self.confirm_sends
            delay = RETRY_DELAYS[failures - 1] if 0 < failures <= len(RETRY_DELAYS) else None
            self.confirm_next_retry_at = now + delay if delay else None
        else:
            self.confirm_sent_at = now
            self.confirm_sends += 1
            self.confirm_next_retry_at = None
        self.save(update_fields=["confirm_attempts", "confirm_last_attempt_at", "confirm_last_error",
                                 "confirm_next_retry_at", "confirm_sent_at", "confirm_sends"])

    def public_extras(self):
        fields = self.letter.fields.filter(public=True)
        return [(f, self.extras.get(f.key)) for f in fields if self.extras.get(f.key)]


class EmailLog(models.Model):
    letter = models.ForeignKey(Letter, on_delete=models.CASCADE, related_name="emails")
    signature = models.ForeignKey(Signature, null=True, blank=True, on_delete=models.SET_NULL, related_name="emails")
    to = models.EmailField()
    subject = models.CharField(max_length=200)
    sent_at = models.DateTimeField(auto_now_add=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-sent_at"]

    def __str__(self):
        return f"{self.to}: {self.subject} ({'failed' if self.error else 'sent'})"
