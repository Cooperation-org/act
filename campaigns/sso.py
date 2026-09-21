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
from django.shortcuts import render
from django.urls import reverse

from linkedtrust_auth.views import CallbackView

from .auth import SsoError, upsert_sso_user


# The linkedtrust_auth callback redirects failures to {frontend}/login?error=<code>.
# Without this route that URL 404s; here it becomes a plain-language help page.
_LOGIN_MESSAGES = {
    "invite_invalid": (
        "This invite is for a different account",
        "You're signed in to LinkedTrust as a different account than this invitation is for. "
        "Open your invite link again in a private / incognito window — or sign out at "
        "live.linkedtrust.us first — then reopen the link with the right account."),
    "invite_required": (
        "An invitation is required",
        "This site is invite-only. Ask an organiser for your own invite link."),
    "auth_failed": (
        "Sign-in didn't finish",
        "Something interrupted the sign-in. Please open your invite link and try again."),
    "state_mismatch": (
        "That sign-in link expired",
        "The sign-in attempt timed out or was already used. Please open your invite link again."),
}


def login_help(request):
    """Friendly page for the SSO failure redirect (/login?error=<code>)."""
    title, message = _LOGIN_MESSAGES.get(
        request.GET.get("error", ""),
        ("Sign-in didn't finish", "Please open your invite link and try again."))
    return render(request, "campaigns/login_help.html", {"page_title": title, "message": message})


class SessionCallbackView(CallbackView):
    def get_or_create_user(self, userinfo):
        # Raised SsoError is caught by the base callback and turned into an error redirect.
        self._sso_user = upsert_sso_user(userinfo)
        return self._sso_user, {}

    def _success(self, request, tokens):
        login(request, self._sso_user)
        return HttpResponseRedirect(self._safe_next(request))

    def _safe_next(self, request):
        # A page that started sign-in (e.g. the vouch form) stashes where to return in the
        # session, since the IdP round-trip drops any ?next on our redirect endpoint.
        nxt = request.session.pop("act_post_login_next", "") or request.GET.get("next") or ""
        # Only allow same-site relative paths, never an open redirect.
        if nxt.startswith("/") and not nxt.startswith("//") and not urlparse(nxt).netloc:
            return nxt
        return reverse("admin:index") if settings.LINKEDTRUST_SSO_ENABLED else "/"
