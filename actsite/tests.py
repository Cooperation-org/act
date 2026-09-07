from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from .urls import build_urlpatterns


def _names(patterns):
    return [str(p.pattern) for p in patterns]


class DeploymentAppsTests(TestCase):
    def test_both_apps_route_campaigns_at_root(self):
        self.assertEqual(_names(build_urlpatterns(["campaigns", "letters"])), ["admin/", "letters/", ""])

    def test_letters_only_forwards_root_to_letters(self):
        patterns = build_urlpatterns(["letters"])
        self.assertEqual(_names(patterns), ["admin/", "letters/", ""])
        self.assertEqual(patterns[-1].name, "home")

    def test_campaigns_only_has_no_letters_route(self):
        self.assertEqual(_names(build_urlpatterns(["campaigns"])), ["admin/", ""])

    @override_settings(ENABLED_APPS=["letters"])
    def test_admin_hides_disabled_app(self):
        User.objects.create_superuser("admin", "a@example.org", "pw")
        self.client.login(username="admin", password="pw")
        r = self.client.get("/admin/")
        self.assertContains(r, "/admin/letters/letter/")
        self.assertNotContains(r, "/admin/campaigns/campaign/")
        self.assertContains(r, "/admin/campaigns/org/")
        self.assertEqual(self.client.get("/admin/campaigns/campaign/").status_code, 403)

    @override_settings(ENABLED_APPS=["campaigns"])
    def test_admin_hides_letters_when_campaigns_only(self):
        User.objects.create_superuser("admin", "a@example.org", "pw")
        self.client.login(username="admin", password="pw")
        r = self.client.get("/admin/")
        self.assertContains(r, "/admin/campaigns/campaign/")
        self.assertNotContains(r, "/admin/letters/letter/")
