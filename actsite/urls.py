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
    if "blog" in enabled:
        patterns.append(path("blog/", include("blog.urls")))
    if "people" in enabled:
        patterns.append(path("people/", include("people.urls")))
    if "campaigns" in enabled:
        patterns.append(path("", include("campaigns.urls")))
    elif "letters" in enabled:
        patterns.append(path("", RedirectView.as_view(pattern_name="letters:index"), name="home"))
    return patterns


urlpatterns = build_urlpatterns(settings.ENABLED_APPS)
if settings.LINKEDTRUST_SSO_ENABLED:
    from campaigns.sso import SessionCallbackView, login_help
    # Our session-login callback must shadow the package's token-only default, so it is
    # registered on the same path BEFORE the package include (first match wins).
    urlpatterns.insert(1, path("api/v1/auth/linkedtrust/callback", SessionCallbackView.as_view()))
    urlpatterns.insert(2, path("api/v1/auth/linkedtrust/", include("linkedtrust_auth.urls")))
    # Landing page for the callback's failure redirect ({frontend}/login?error=…), which
    # would otherwise 404. Adds a route only; the success path is untouched.
    urlpatterns.insert(3, path("login", login_help, name="login_help"))
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
