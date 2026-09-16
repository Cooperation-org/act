"""LinkedTrust SSO user handler (used only when SSO env vars are set, see settings).

Security model, mirroring GovKit (apps/accounts/auth_handlers.py) — identity is resolved
explicitly, never inferred:

  1. Match on the stable OIDC subject (SsoIdentity.provider+sub). This is what makes a
     re-login return the same account without trusting the email.
  2. Else, if the provider asserts a VERIFIED email, link it to an existing account ONLY
     when that account is an unclaimed, unprivileged, password-less placeholder. A verified
     email must NEVER take over a staff/superuser or password-holding account — otherwise
     anyone who registers that email at an IdP inherits it.
  3. Else create a new, unprivileged, password-less user.

Admin/volunteer rights come only from the signed `invite` claim the callback injects
(campaigns.sso), not from a bare email match.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction

from .models import SsoIdentity, Org, VolunteerProfile

PROVIDER = "linkedtrust"


class SsoError(Exception):
    """The SSO payload lacked what we need, or would require an unsafe account takeover."""


def _unique_username(User, email):
    base = email.split("@")[0].replace(".", "_").replace("+", "_")[:24] or "user"
    username, n = base, 1
    while User.objects.filter(username__iexact=username).exists():
        username, n = f"{base}_{n}", n + 1
    return username


@transaction.atomic
def upsert_sso_user(userinfo):
    """Resolve or create the local user for a LinkedTrust login, then apply any invite role."""
    User = get_user_model()
    sub = (userinfo.get("sub") or "").strip()
    if not sub:
        raise SsoError("SSO payload has no subject ('sub').")
    email = (userinfo.get("email") or "").strip().lower()
    verified = bool(userinfo.get("email_verified"))

    # 1. Stable subject → same account every time.
    ident = SsoIdentity.objects.select_related("user").filter(provider=PROVIDER, sub=sub).first()
    if ident:
        user = ident.user
    else:
        user = _resolve_by_email_or_create(User, email, verified, userinfo)
        SsoIdentity.objects.create(provider=PROVIDER, sub=sub, user=user)

    _apply_invite(user, userinfo.get("invite") or {})
    return user


def _resolve_by_email_or_create(User, email, verified, userinfo):
    if email and verified:
        existing = User.objects.filter(email__iexact=email).first()
        if existing:
            # M2: never auto-claim a privileged or credentialed account by email alone.
            if existing.is_staff or existing.is_superuser or existing.has_usable_password():
                raise SsoError("An account with this email already exists. Sign in with your "
                               "existing credentials to link SSO.")
            return existing  # unprivileged, password-less placeholder — safe to claim
    if not email:
        raise SsoError("LinkedTrust account has no email address.")
    if User.objects.filter(email__iexact=email).exists():
        # Email is taken but the provider did not assert it as verified — refuse, don't hijack.
        raise SsoError("An account with this email exists but the provider did not verify it.")
    user = User(username=_unique_username(User, email), email=email,
                first_name=(userinfo.get("name") or "")[:150])
    user.set_unusable_password()  # SSO users never log in with a password
    user.save()
    return user


def _apply_invite(user, invite):
    """Grant access from the signed invite. `r` = role; `a` = app slugs (already checked)."""
    role = (invite or {}).get("r") or ""
    role = role.lower()
    if role in ("admin", "superuser", "owner"):
        if not (user.is_staff and user.is_superuser):
            user.is_staff = user.is_superuser = True
            user.save(update_fields=["is_staff", "is_superuser"])
    elif role in ("approver", "volunteer", "editor"):
        changed = False
        if not user.is_staff:
            user.is_staff = True
            changed = True
        if changed:
            user.save(update_fields=["is_staff"])
        user.groups.add(Group.objects.get_or_create(name="Volunteers")[0])
        # Scope them to every org this deployment carries (one, for the Jreas site).
        for org in Org.objects.all():
            VolunteerProfile.objects.get_or_create(
                user=user, org=org, defaults={"is_approver": role == "approver"})


# Back-compat name referenced by settings.LINKEDTRUST_USER_HANDLER when the package's
# default (token-issuing) CallbackView is used. act uses campaigns.sso.SessionCallbackView
# for real session login, but keep this callable working and safe.
def get_or_create_user(userinfo):
    return upsert_sso_user(userinfo), {}
