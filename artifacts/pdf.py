"""A story as a PDF, rendered by WeasyPrint (as letters do). Photos are read from
storage, never fetched over the web, so a draft prints the same as a published story."""
import mimetypes
from urllib.parse import parse_qs, unquote, urlsplit

from django.conf import settings
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.files.storage import default_storage
from django.template.loader import render_to_string
from weasyprint import HTML
from weasyprint.urls import URLFetcher, URLFetcherResponse

PDF_PHOTO_WIDTH = 1200


class _StorageFetcher(URLFetcher):
    """STATIC_URL from disk; MEDIA_URL from storage (?w= gives the thumbnail). Nothing
    else is fetched: a story's PDF never reaches out to another site."""

    def __call__(self, url):
        from .views import _thumb
        parts = urlsplit(url)
        path = unquote(parts.path)
        if path.startswith(settings.STATIC_URL):
            name = path[len(settings.STATIC_URL):]
            found = staticfiles_storage.path(name) if staticfiles_storage.exists(name) else finders.find(name)
            if found:
                return URLFetcherResponse(url=url, body=open(found, "rb"),
                                          headers={"content-type": mimetypes.guess_type(found)[0] or ""})
        if path.startswith(settings.MEDIA_URL):
            name = path[len(settings.MEDIA_URL):]
            width = (parse_qs(parts.query).get("w") or [""])[0]
            if width.isdigit():
                name = _thumb(name, int(width)) or name
            return URLFetcherResponse(url=url, body=default_storage.open(name, "rb"),
                                      headers={"content-type": mimetypes.guess_type(name)[0] or "image/jpeg"})
        raise ValueError(f"not fetched for a PDF: {url}")


def story_pdf(story, blocks, page_url):
    html = render_to_string("artifacts/story_pdf.html", {
        "s": story, "blocks": blocks, "page_url": page_url, "photo_width": PDF_PHOTO_WIDTH,
        "pieces": [b.artifact for b in blocks if b.artifact],
    })
    return HTML(string=html, base_url=page_url, url_fetcher=_StorageFetcher()).write_pdf()
