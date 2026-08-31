"""LinkedTrust SSO user handler (used only when SSO env vars are set — see settings)."""
from django.contrib.auth import get_user_model


def get_or_create_user(userinfo):
    User = get_user_model()
    email = userinfo.get("email") or ""
    username = userinfo.get("preferred_username") or email or userinfo.get("sub")
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={"email": email, "first_name": (userinfo.get("name") or "")[:150]},
    )
    return user, {}
