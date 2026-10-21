"""Live demo: each visitor gets their own copy of the sample org (settings.ACT_DEMO_ORG),
signed in as a guest, so they can touch everything without changing anyone else's.

Records are copied; files are not. A copy points at the sample's files, and only files
uploaded into the copy (stored under the copy's own folder) are deleted with it.
"""
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from campaigns.models import Org, VolunteerProfile

from .models import Artifact, Collection, DemoCopy, Source, Story, StoryBlock, Tag

KEEP = timedelta(hours=24)


def sample_org():
    slug = settings.ACT_DEMO_ORG
    return Org.objects.filter(slug=slug).first() if slug else None


@transaction.atomic
def make_copy(sample):
    key = secrets.token_hex(4)
    guest = get_user_model().objects.create_user(f"demo-{key}")  # no password: signs in only through the demo link
    org = Org.objects.create(slug=f"{sample.slug}-demo-{key}", name=sample.name, website=sample.website)
    VolunteerProfile.objects.create(user=guest, org=org, is_approver=True)
    DemoCopy.objects.create(org=org, guest=guest)
    sources = {}
    for s in Source.objects.filter(org=sample):
        old = s.pk
        s.pk, s.org, s.last_checked, s.last_error = None, org, None, ""
        s.save()
        sources[old] = s
    tags = {}
    for t in Tag.objects.filter(org=sample):
        old = t.pk
        t.pk, t.org = None, org
        t.save()
        tags[old] = t
    arts = {}
    for a in Artifact.objects.filter(org=sample).prefetch_related("tags"):
        old, old_tags = a.pk, [tags[t.pk] for t in a.tags.all()]
        a.pk, a.org, a.source = None, org, sources.get(a.source_id)
        a.save()
        a.tags.set(old_tags)
        arts[old] = a
    cols = {}
    for c in Collection.objects.filter(org=sample).prefetch_related("artifacts"):
        old, members = c.pk, [arts[x.pk] for x in c.artifacts.all() if x.pk in arts]
        c.pk, c.org = None, org
        c.save()
        c.artifacts.set(members)
        cols[old] = c
    for st in Story.objects.filter(org=sample).prefetch_related("blocks"):
        blocks = list(st.blocks.all())
        st.pk, st.org, st.collection = None, org, cols.get(st.collection_id)
        st.slug = f"{st.slug[:40]}-{key}"
        st.save()
        StoryBlock.objects.bulk_create([StoryBlock(story=st, order=b.order, text=b.text, by_ai=b.by_ai,
                                                   artifact=arts.get(b.artifact_id)) for b in blocks])
    return guest, org


def _remove_folder(prefix):
    """The copy's own upload folder, once its files are gone (local disk only; object
    storage has no folders)."""
    import os
    import shutil
    from django.core.files.storage import default_storage
    try:
        path = default_storage.path(prefix)
    except NotImplementedError:
        return
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


def remove_old(now=None):
    """Delete demo copies older than KEEP, with their guest logins and their own files."""
    cutoff = (now or timezone.now()) - KEEP
    gone = 0
    for d in DemoCopy.objects.filter(created__lt=cutoff).select_related("org", "guest"):
        own = f"artifacts/{d.org.slug}/"
        for a in Artifact.objects.filter(org=d.org).exclude(file=""):
            if a.file.name.startswith(own):
                a.file.delete(save=False)
        _remove_folder(own)
        guest = d.guest
        d.org.delete()
        guest.delete()
        gone += 1
    return gone
