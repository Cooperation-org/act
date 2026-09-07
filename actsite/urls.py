from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView


def build_urlpatterns(enabled):
    """Routes for the apps this deployment serves. campaigns owns "/"; without it
    "/" forwards to the letters index."""
    patterns = [path("admin/", admin.site.urls)]
    if "letters" in enabled:
        patterns.append(path("letters/", include("letters.urls")))
    if "campaigns" in enabled:
        patterns.append(path("", include("campaigns.urls")))
    elif "letters" in enabled:
        patterns.append(path("", RedirectView.as_view(pattern_name="letters:index"), name="home"))
    return patterns


urlpatterns = build_urlpatterns(settings.ENABLED_APPS)
if settings.LINKEDTRUST_SSO_ENABLED:
    urlpatterns.insert(1, path("api/v1/auth/linkedtrust/", include("linkedtrust_auth.urls")))
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
