"""Story Kit: raw material from many sources, kept, labeled, assembled into stories.

Design: projects repo Ideas/inbound-artifacts/ and Ideas/story-kit/.

The source is the original (an email, a Drive file, a LinkedClaim, a phone upload).
An Artifact is our record of one source item plus a copy of its file, because the
original may be deleted. One record per item: the same file sent twice is one
Artifact (sha256, per org). The sender's words are kept exactly as sent.
"""
import hashlib

from django.conf import settings
from django.db import models
from django.utils import timezone

from campaigns.models import Org


class SourceKind(models.TextChoices):
    UPLOAD = "upload", "Phone or computer"
    EMAIL = "email", "Email"
    DRIVE = "drive", "Google Drive folder"
    LINKEDCLAIMS = "linkedclaims", "LinkedClaims"
    LINK = "link", "Link"


class Source(models.Model):
    """Where an org's material comes in. `config` holds what the kind needs:
    email → {"address"}, drive → {"folder_id", "folder_url"}, linkedclaims → {"subject"}."""
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="sources")
    kind = models.CharField(max_length=16, choices=SourceKind.choices)
    label = models.CharField(max_length=200, blank=True)
    config = models.JSONField(default=dict, blank=True)
    last_checked = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created"]

    def __str__(self):
        return f"{self.org.slug}: {self.label or self.get_kind_display()}"


class ArtifactKind(models.TextChoices):
    PHOTO = "photo", "Photo"
    VIDEO = "video", "Video"
    VOICE = "voice", "Voice"
    TEXT = "text", "Text"
    FILE = "file", "File"
    LINK = "link", "Link"
    CLAIM = "claim", "LinkedClaim"
    DATA = "data", "Number"


class ArtifactStatus(models.TextChoices):
    NEW = "new", "New"
    KEPT = "kept", "Kept"
    HIDDEN = "hidden", "Hidden"


class TagGroup(models.TextChoices):
    LABEL = "", "Label"
    PART = "part", "Part of our org"


class Tag(models.Model):
    """A label. Group "part" marks a part of the org (a chapter, a site, a program,
    a partner) so the library can filter by it separately from topic labels."""
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="artifact_tags")
    name = models.CharField(max_length=60)
    group = models.CharField(max_length=8, choices=TagGroup.choices, default=TagGroup.LABEL, blank=True)
    # A part of the org can sit inside another: a business inside a site.
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children")

    class Meta:
        ordering = ["group", "name"]
        constraints = [models.UniqueConstraint(fields=["org", "name"], name="one_tag_name_per_org")]

    def __str__(self):
        return self.name


def artifact_path(instance, filename):
    return f"artifacts/{instance.org.slug}/{instance.sha256[:2]}/{instance.sha256}/{filename}"


class Artifact(models.Model):
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="artifacts")
    source = models.ForeignKey(Source, null=True, blank=True, on_delete=models.SET_NULL, related_name="artifacts")
    kind = models.CharField(max_length=10, choices=ArtifactKind.choices)
    source_ref = models.CharField(max_length=300, blank=True, help_text="Id at the source: Message-ID, Drive file id, claim id")
    source_url = models.URLField(max_length=1000, blank=True, help_text="Where the original lives")
    happened_at = models.DateTimeField(null=True, blank=True, help_text="When it happened (photo metadata, email date, claim date)")
    sent_by = models.CharField(max_length=300, blank=True, help_text="Login, email sender, or claim source")
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="artifacts_added")
    received_at = models.DateTimeField(default=timezone.now)
    words = models.TextField(blank=True, help_text="The sender's own words, exactly as sent")
    file = models.FileField(upload_to=artifact_path, max_length=500, blank=True)
    filename = models.CharField(max_length=300, blank=True)
    content_type = models.CharField(max_length=120, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64)
    title = models.CharField(max_length=300, blank=True)
    about = models.CharField(max_length=300, blank=True, help_text="Who or what this is about")
    consent = models.BooleanField(default=False, help_text="Everyone shown agreed to be in it")
    status = models.CharField(max_length=8, choices=ArtifactStatus.choices, default=ArtifactStatus.NEW)
    tags = models.ManyToManyField(Tag, blank=True, related_name="artifacts")
    # [{"label": "...", "by": "ai"|"<login>", "confirmed": bool}]
    ai_labels = models.JSONField(default=list, blank=True)
    claim_id = models.PositiveBigIntegerField(null=True, blank=True, help_text="LinkedTrust claim id, when this is or became a claim")

    class Meta:
        ordering = ["-received_at"]
        constraints = [models.UniqueConstraint(fields=["org", "sha256"], name="one_artifact_per_item_per_org")]

    def __str__(self):
        return self.title or self.filename or self.source_url or f"{self.get_kind_display()} {self.pk}"

    @property
    def when(self):
        return self.happened_at or self.received_at

    @property
    def is_image(self):
        return self.kind == ArtifactKind.PHOTO and bool(self.file)

    @property
    def is_video(self):
        return self.kind == ArtifactKind.VIDEO and bool(self.file)

    @property
    def is_audio(self):
        return self.kind == ArtifactKind.VOICE and bool(self.file)

    @property
    def claim_url(self):
        return f"{settings.LT_API}/claims/{self.claim_id}" if self.claim_id else ""

    @staticmethod
    def digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()


class Collection(models.Model):
    """A labeled set of artifacts: picked by hand, a saved filter, or both."""
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="collections")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    # The library filter it was made from: {"kind", "tag", "source", "person", "q", "since", "until"}
    filter = models.JSONField(default=dict, blank=True)
    artifacts = models.ManyToManyField(Artifact, blank=True, related_name="collections")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return self.name


class StoryStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"


class StoryTemplate(models.TextChoices):
    STORY = "", "Story"
    TIMELINE = "timeline", "Timeline"
    ONEPAGE = "onepage", "One page"


class Story(models.Model):
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="stories")
    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    statement = models.TextField(blank=True, help_text="What this story shows, in one or two sentences")
    collection = models.ForeignKey(Collection, null=True, blank=True, on_delete=models.SET_NULL, related_name="stories")
    status = models.CharField(max_length=10, choices=StoryStatus.choices, default=StoryStatus.DRAFT)
    template = models.CharField(max_length=10, choices=StoryTemplate.choices, default=StoryTemplate.STORY, blank=True,
                                help_text="How the published page lays the pieces out")
    published_at = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated"]
        verbose_name_plural = "stories"

    def __str__(self):
        return self.title


class StoryBlock(models.Model):
    """One piece of a story, in order: an artifact, or the editor's text, or both
    (a caption written for this story)."""
    story = models.ForeignKey(Story, on_delete=models.CASCADE, related_name="blocks")
    order = models.PositiveIntegerField(default=0)
    artifact = models.ForeignKey(Artifact, null=True, blank=True, on_delete=models.SET_NULL, related_name="story_blocks")
    text = models.TextField(blank=True)
    by_ai = models.BooleanField(default=False, help_text="Drafted by the AI helper and not yet edited")

    class Meta:
        ordering = ["order", "pk"]


class DemoCopy(models.Model):
    """A visitor's own copy of the sample org, made when they open the live demo without
    signing in. Copies older than a day are removed, with their guest login and any
    files uploaded into them."""
    org = models.OneToOneField(Org, on_delete=models.CASCADE, related_name="demo_copy")
    guest = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="demo_copies")
    created = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"demo {self.org.slug}"
