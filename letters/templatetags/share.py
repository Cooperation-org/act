"""Share links for a letter: {% share letter %}.

Builds the prefilled message and one intent URL per network. Networks whose
intents cannot carry text (Instagram) or need a home server (Mastodon) get a
sensible fallback href here and are improved by share.js in the browser.
"""
from urllib.parse import quote

from django import template
from django.urls import reverse

register = template.Library()


def share_text(letter, url):
    kind = "petition" if letter.kind == "petition" else "open letter"
    return f"Would you like to consider this {kind} {letter.title}, I have signed it.  What do you think? {url}"


@register.inclusion_tag("letters/share.html", takes_context=True)
def share(context, letter):
    request = context["request"]
    url = request.build_absolute_uri(reverse("letters:letter", kwargs={"slug": letter.slug}))
    text = share_text(letter, url)
    q = lambda s: quote(s, safe="")  # noqa: E731
    return {
        "text": text,
        "url": url,
        "links": {
            "bluesky": f"https://bsky.app/intent/compose?text={q(text)}",
            "mastodon": f"https://mastodonshare.com/?text={q(text)}",
            "instagram": "https://www.instagram.com/",
            "linkedin": f"https://www.linkedin.com/feed/?shareActive=true&text={q(text)}",
            "facebook": f"https://www.facebook.com/sharer/sharer.php?u={q(url)}&quote={q(text)}",
            "email": f"mailto:?subject={q(letter.title)}&body={q(text)}",
        },
    }
