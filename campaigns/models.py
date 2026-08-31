"""act data model — see projects repo Active/raise-the-voices/act-design.md.

Boundaries: money is NEVER handled here (Givebutter or SimpleTip embeds only);
testimonials reference LinkedClaims by URI; subscribers sync to the CRM (not yet wired).
"""
import secrets

from django.conf import settings
from django.db import models


class PublishStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PENDING = "pending", "Pending review"
    PUBLISHED = "published", "Published"


class Org(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=200)
    website = models.URLField(blank=True)
    # Givebutter account that owns this org's campaigns (charity of record's account)
    givebutter_account_id = models.CharField(max_length=100, blank=True,
                                             help_text="Account ID from Givebutter dashboard (script embed)")

    def __str__(self):
        return self.name


class VolunteerProfile(models.Model):
    """Links a login to an org. Admin visibility is scoped to profile.org."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="volunteer")
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="volunteers")
    is_approver = models.BooleanField(default=False, help_text="May publish pending content")

    def __str__(self):
        return f"{self.user} @ {self.org.slug}"


class Campaign(models.Model):
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="campaigns")
    slug = models.SlugField(unique=True)
    title = models.CharField(max_length=200)
    organizer = models.CharField(max_length=200, blank=True)
    location = models.CharField(max_length=200, blank=True)
    summary = models.TextField(blank=True, help_text="Short text for listings and share cards")
    story = models.TextField(blank=True, help_text="The campaign's own words. Facts only; cite sources.")
    photo = models.ImageField(upload_to="campaigns/", blank=True)
    source_url = models.URLField(blank=True, help_text="Where the story facts come from")
    status = models.CharField(max_length=12, choices=PublishStatus.choices, default=PublishStatus.DRAFT)
    # money embeds — the page only WRAPS these; act never touches funds
    givebutter_campaign_id = models.CharField(max_length=100, blank=True,
                                             help_text="Widget ID from Givebutter dashboard → Developers → Widgets")
    simpletip_receiver = models.SlugField(blank=True)
    simpletip_api = models.URLField(blank=True)
    created = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class CTA(models.Model):
    class Kind(models.TextChoices):
        GIVE = "give", "Give"
        MENTOR = "mentor", "Mentor / practice English"
        HIRE = "hire", "Hire someone"
        EVENT = "event", "Host an event"
        AMA = "ama", "Ask Me Anything"
        SUBSCRIBE = "subscribe", "Get updates"

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="ctas")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    title = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    button_label = models.CharField(max_length=40, default="Sign up")
    sort = models.PositiveSmallIntegerField(default=0)
    enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort", "id"]
        verbose_name = "Call to action"
        verbose_name_plural = "Calls to action"

    def __str__(self):
        return f"{self.campaign.slug}: {self.title}"


class Response(models.Model):
    """A person answering a non-money CTA. Contact facts sync to the CRM (not yet wired)."""
    cta = models.ForeignKey(CTA, on_delete=models.CASCADE, related_name="responses")
    name = models.CharField(max_length=200)
    email = models.EmailField()
    message = models.TextField(blank=True)
    handled = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} → {self.cta}"


class Testimonial(models.Model):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="testimonials")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    quote = models.TextField()
    video = models.FileField(upload_to="testimony/", blank=True)
    display_name = models.CharField(max_length=200, blank=True,
                                    help_text="Shown publicly ONLY if show_identity is on")
    show_identity = models.BooleanField(default=False,
                                        help_text="Opt-in. Names/faces tied to funding can endanger people.")
    relationship = models.CharField(max_length=200, blank=True, help_text="e.g. 'donor since June'")
    linkedclaim_uri = models.URLField(blank=True, help_text="Signed LinkedClaim for this testimony")
    status = models.CharField(max_length=12, choices=PublishStatus.choices, default=PublishStatus.PENDING)
    created = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"testimony for {self.campaign.slug} ({self.status})"


class Update(models.Model):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="updates")
    date = models.DateField()
    text = models.TextField(help_text="Facts: receipts, counts, milestones")
    status = models.CharField(max_length=12, choices=PublishStatus.choices, default=PublishStatus.DRAFT)

    class Meta:
        ordering = ["-date"]

    def __str__(self):
        return f"{self.campaign.slug} {self.date}"


def _share_code():
    return secrets.token_urlsafe(6)


class ShareLink(models.Model):
    """Per-person share link with counted attribution."""
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="share_links")
    volunteer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    code = models.CharField(max_length=16, unique=True, default=_share_code)
    clicks = models.PositiveIntegerField(default=0)
    created = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"/s/{self.code} → {self.campaign.slug} ({self.clicks})"
