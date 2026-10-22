import re

from django import template
from django.utils.html import format_html

register = template.Library()


@register.simple_tag
def ic(name, cls=""):
    """A line icon from the sprite in _icons.html. Decorative: the words beside it carry the meaning."""
    return format_html('<svg class="ic{}" aria-hidden="true" focusable="false"><use href="#i-{}"></use></svg>',
                       f" {cls}" if cls else "", name)


KIND_ICONS = {"photo": "camera", "video": "video", "voice": "mic", "text": "note", "link": "link",
              "file": "file", "claim": "signed", "data": "number"}


@register.filter
def kind_icon(kind):
    """The icon name for an artifact kind."""
    return KIND_ICONS.get(kind, "file")


HOW_SENT = {"upload": "Sent from a phone or computer", "email": "Sent by email", "drive": "Copied from a Google Drive folder",
            "linkedclaims": "A signed LinkedClaim", "link": "A link someone shared"}


@register.filter
def how_sent(artifact):
    """How a piece reached the team, in plain words and with no names, for the public story page."""
    source = getattr(artifact, "source", None)
    return HOW_SENT.get(getattr(source, "kind", ""), "Sent in")


KIND_LABELS = {"photo": "photo", "video": "video", "voice": "voice", "text": "note", "link": "link",
               "file": "file", "claim": "claim", "data": "number"}


@register.filter
def kind_label(kind):
    """The short word on a kind tile: VOICE, PHOTO, LINK."""
    return KIND_LABELS.get(kind, kind)


@register.filter
def state_of(a):
    """(css class, words) for where a piece stands: in a story, signed, waiting in the inbox, or kept.
    The library view marks pieces with in_story so a list of them costs one query."""
    if getattr(a, "in_story", False):
        return ("", "In a story")
    if a.claim_id:
        return ("signed", "Signed")
    if a.status == "new":
        return ("inbox", "In inbox")
    return ("kept", "Kept")


def _initials(user):
    name = (user.get_full_name() or "").strip()
    if user.get_username().startswith("demo-"):
        return "ME"
    words = name.split() or [w for w in re.split(r"[._+\-\s]+", (user.email or user.get_username()).split("@")[0]) if w]
    if len(words) >= 2:
        return (words[0][0] + words[-1][0]).upper()
    return (words[0][:2] if words else "?").upper()


@register.simple_tag(takes_context=True)
def shell(context):
    """What the frame around every page needs: your initials, who is waiting in the inbox, which
    orgs you can switch to, and the address of your own uploads."""
    from campaigns.admin import user_orgs
    from artifacts.models import Artifact, ArtifactStatus
    request, org = context.get("request"), context.get("org")
    user = getattr(request, "user", None)
    if not org or user is None or not user.is_authenticated:
        return {}
    orgs = list(user_orgs(user).order_by("name"))
    return {
        "initials": _initials(user),
        "inbox": Artifact.objects.filter(org=org, status=ArtifactStatus.NEW).count(),
        "mine": user.email or user.get_username(),
        "orgs": orgs if len(orgs) > 1 else [],
    }


@register.filter
def short_who(sent_by):
    """Who sent it, kept short for a card: jordan.lee@org.example becomes Jordan Lee."""
    who = (sent_by or "").split("@")[0]
    return " ".join(w.capitalize() for w in re.split(r"[._+\-]+", who) if w) or ""
