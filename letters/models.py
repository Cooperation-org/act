"""Signable documents: open letters and petitions.

A Letter belongs to an Org. It defines which optional fields a signer is asked
for (SignatureField). A Signature counts once the signer has either confirmed
their email or drawn a signature. EmailLog records every mail attempt so an
admin can see who is stuck confirming.
"""
import secrets

from django.db import models
from django.db.models import Q

from campaigns.models import Org, PublishStatus


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
        return self.filter(Q(confirmed_at__isnull=False) | ~Q(drawn=""))

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
        return bool(self.confirmed_at or self.drawn)

    @property
    def confirmation(self):
        if self.confirmed_at:
            return "confirmed"
        if self.drawn:
            return "drawn"
        if self.email and self.confirm_sent_at:
            return "waiting"
        return "unsent"

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
