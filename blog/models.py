"""Posts: an org's blog. Markdown body, optional author profile, publish-gated."""
from django.db import models
from django.utils import timezone

from campaigns.models import Org, PublishStatus
from people.models import Person


class Post(models.Model):
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="posts")
    slug = models.SlugField()
    title = models.CharField(max_length=200)
    author = models.ForeignKey(Person, null=True, blank=True, on_delete=models.SET_NULL, related_name="posts",
                               help_text="Shown only if the author's profile is published")
    summary = models.CharField(max_length=300, blank=True, help_text="One or two sentences for lists and the feed")
    body = models.TextField(help_text="Markdown")
    photo = models.ImageField(upload_to="posts/", blank=True)
    status = models.CharField(max_length=12, choices=PublishStatus.choices, default=PublishStatus.DRAFT)
    published_at = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-published_at", "-created"]
        constraints = [models.UniqueConstraint(fields=["org", "slug"], name="post_slug_per_org")]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if self.status == "published" and self.published_at is None:
            self.published_at = timezone.now()
        super().save(*args, **kwargs)

    @property
    def public_author(self):
        a = self.author
        return a if a and a.status == "published" and a.consent_on_record else None
