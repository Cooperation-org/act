"""Letter + signatures as a PDF, rendered by WeasyPrint from the same parchment
template as the page. Static and media URLs are read from disk, never fetched."""
import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlsplit

from django.conf import settings
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import staticfiles_storage
from django.template.loader import render_to_string
from weasyprint import HTML
from weasyprint.urls import URLFetcher, URLFetcherResponse

from .markup import render


def _file(path):
    return URLFetcherResponse(
        url=str(path), body=open(path, "rb"),
        headers={"content-type": mimetypes.guess_type(str(path))[0] or "application/octet-stream"})


class _LocalFetcher(URLFetcher):
    """Serve STATIC_URL / MEDIA_URL from disk; delegate everything else to WeasyPrint's default."""

    def __call__(self, url):
        path = unquote(urlsplit(url).path)
        if path.startswith(settings.STATIC_URL):
            name = path[len(settings.STATIC_URL):]
            # collected (possibly hashed) name first, then the app's source file
            found = staticfiles_storage.path(name) if staticfiles_storage.exists(name) else finders.find(name)
            if found:
                return _file(found)
        if path.startswith(settings.MEDIA_URL):
            file = Path(settings.MEDIA_ROOT) / path[len(settings.MEDIA_URL):]
            if file.is_file():
                return _file(file)
        return super().__call__(url)


def letter_pdf(letter, signatures, base_url):
    html = render_to_string("letters/pdf.html", {
        "letter": letter,
        "body_html": render(letter.body),
        "signatures": list(signatures),
        "count": letter.count(),
    })
    return HTML(string=html, base_url=base_url, url_fetcher=_LocalFetcher()).write_pdf()
