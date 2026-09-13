"""Template context: which org this deployment is (single-org site mode) and
which apps it serves, so base.html can build its nav from data, not guesses."""
from django.conf import settings

from .models import Campaign, Org


def site_org():
    """The Org this deployment is branded as, or None in platform mode."""
    if not settings.SITE_ORG_SLUG:
        return None
    return Org.objects.filter(slug=settings.SITE_ORG_SLUG).first()


def site_campaigns(org=None):
    """Published campaigns visible on this deployment."""
    qs = Campaign.objects.filter(status="published").select_related("org")
    org = org or site_org()
    return qs.filter(org=org) if org else qs


def site(request):
    org = site_org()
    give = site_campaigns(org).order_by("created").first() if org else None
    return {
        "site_org": org,
        "site_give_campaign": give,
        "enabled_apps": settings.ENABLED_APPS,
    }
