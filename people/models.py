"""People associated with an org: members, staff, volunteers, mentors.

A profile names a person in public. For orgs working somewhere dangerous, a
name tied to funding can endanger someone, so a profile publishes only when
the person's consent is on record AND an approver publishes it.
"""
from django.conf import settings
from django.db import models

from campaigns.models import Org, PublishStatus


class Person(models.Model):
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="people")
    slug = models.SlugField()
    name = models.CharField(max_length=200)
    role = models.CharField(max_length=200, blank=True, help_text="e.g. Director, Member, Mentor")
    bio = models.TextField(blank=True, help_text="Markdown. The person's own words.")
    photo = models.ImageField(upload_to="people/", blank=True)
    links = models.TextField(blank=True, help_text="One URL per line (site, LinkedIn, Bluesky…)")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="person_profiles", help_text="Login this profile belongs to, if any")
    simpletip_receiver = models.SlugField(
        blank=True, help_text="SimpleTip receiver id: readers can tip this person's posts (split with the campaign)")
    consent_on_record = models.BooleanField(
        default=False, help_text="The person agreed, in writing, to be named on this site. Required to publish.")
    status = models.CharField(max_length=12, choices=PublishStatus.choices, default=PublishStatus.DRAFT)
    sort = models.PositiveSmallIntegerField(default=0)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort", "name"]
        constraints = [models.UniqueConstraint(fields=["org", "slug"], name="person_slug_per_org")]
        verbose_name_plural = "people"

    def __str__(self):
        return self.name

    @property
    def link_list(self):
        return [u.strip() for u in self.links.splitlines() if u.strip()]
