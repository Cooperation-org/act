from django import template
from django.utils.html import format_html

register = template.Library()


@register.simple_tag
def ic(name, cls=""):
    """A line icon from the sprite in _icons.html. Decorative: the words beside it carry the meaning."""
    return format_html('<svg class="ic{}" aria-hidden="true" focusable="false"><use href="#i-{}"></use></svg>',
                       f" {cls}" if cls else "", name)


KIND_ICONS = {"photo": "camera", "video": "video", "voice": "mic", "text": "note", "link": "link",
              "file": "file", "claim": "signed", "data": "note"}


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
