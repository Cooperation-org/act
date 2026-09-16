"""Session-login callback for LinkedTrust SSO.

The linkedtrust_auth package's default CallbackView is built for SPA/token flows: it
redirects to a frontend with tokens in the URL fragment and never establishes a Django
session. act is server-rendered with session auth, so we subclass it to call
django.contrib.auth.login() with the user our secure resolver returns, then land them
in /admin/ (or a safe ?next=)."""
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth import login
from django.http import HttpResponseRedirect
from django.urls import reverse

from linkedtrust_auth.views import CallbackView

from .auth import SsoError, upsert_sso_user


class SessionCallbackView(CallbackView):
    def get_or_create_user(self, userinfo):
        # Raised SsoError is caught by the base callback and turned into an error redirect.
        self._sso_user = upsert_sso_user(userinfo)
        return self._sso_user, {}

    def _success(self, request, tokens):
        login(request, self._sso_user)
        return HttpResponseRedirect(self._safe_next(request))

    def _safe_next(self, request):
        nxt = request.GET.get("next") or ""
        # Only allow same-site relative paths, never an open redirect.
        if nxt.startswith("/") and not nxt.startswith("//") and not urlparse(nxt).netloc:
            return nxt
        return reverse("admin:index") if settings.LINKEDTRUST_SSO_ENABLED else "/"
