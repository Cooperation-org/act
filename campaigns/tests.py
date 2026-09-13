from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings

from .models import Campaign, Org, ShareLink, Update, VolunteerProfile


class ActTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_jreas")
        cls.campaign = Campaign.objects.get(slug="jreas")

    def test_draft_campaign_hidden_from_public(self):
        self.assertEqual(self.client.get("/c/jreas/").status_code, 404)
        self.assertNotContains(self.client.get("/"), "JREAS")

    def test_published_campaign_renders_with_disclosure_and_ctas(self):
        Campaign.objects.filter(pk=self.campaign.pk).update(status="published")
        r = self.client.get("/c/jreas/")
        self.assertContains(r, "JREAS Hub")
        self.assertContains(r, "complete discretion and control")  # locked language present
        self.assertContains(r, "Mentor")
        self.assertContains(r, "Add your testimony")

    def test_respond_creates_response(self):
        Campaign.objects.filter(pk=self.campaign.pk).update(status="published")
        cta = self.campaign.ctas.get(kind="mentor")
        r = self.client.post(f"/c/jreas/act/{cta.id}/",
                             {"name": "A Person", "email": "a@example.org", "message": "hi"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(cta.responses.count(), 1)

    def test_testimony_requires_login_and_lands_pending(self):
        Campaign.objects.filter(pk=self.campaign.pk).update(status="published")
        r = self.client.get("/c/jreas/testimony/")
        self.assertEqual(r.status_code, 302)  # to login
        User.objects.create_user("vol", password="x")
        self.client.login(username="vol", password="x")
        self.client.post("/c/jreas/testimony/", {"quote": "I know this is real.", "show_identity": ""})
        t = self.campaign.testimonials.get()
        self.assertEqual(t.status, "pending")
        self.assertFalse(t.show_identity)

    def test_share_link_counts_and_redirects(self):
        Campaign.objects.filter(pk=self.campaign.pk).update(status="published")
        link = ShareLink.objects.create(campaign=self.campaign)
        r = self.client.get(f"/s/{link.code}/")
        self.assertEqual(r.status_code, 302)
        link.refresh_from_db()
        self.assertEqual(link.clicks, 1)

    def test_share_card_png(self):
        Campaign.objects.filter(pk=self.campaign.pk).update(status="published")
        r = self.client.get("/c/jreas/card/story.png")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "image/png")


class SiteModeTests(TestCase):
    """ACT_SITE_ORG=jreas: the deployment is the Jreas Coop site."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_jreas")
        cls.org = Campaign.objects.get(slug="jreas").org
        cls.other = Org.objects.create(slug="other", name="Other Org")
        Campaign.objects.create(org=cls.other, slug="elsewhere", title="Elsewhere Campaign", status="published")

    def test_platform_mode_lists_every_org(self):
        Campaign.objects.filter(slug="jreas").update(status="published")
        r = self.client.get("/")
        self.assertContains(r, "JREAS Hub")
        self.assertContains(r, "Elsewhere Campaign")

    @override_settings(SITE_ORG_SLUG="jreas")
    def test_org_home_brands_and_scopes(self):
        self.org.tagline = "A tagline"; self.org.about = "About **us**"; self.org.save()
        Campaign.objects.filter(slug="jreas").update(status="published")
        r = self.client.get("/")
        self.assertContains(r, "Jreas Coop")
        self.assertContains(r, "A tagline")
        self.assertContains(r, "About <strong>us</strong>")
        self.assertContains(r, "JREAS Hub")
        self.assertNotContains(r, "Elsewhere Campaign")
        self.assertContains(r, "complete discretion and control")
        self.assertContains(r, 'href="/c/jreas/"')  # Give in nav
        self.assertEqual(self.client.get("/c/elsewhere/").status_code, 404)
        self.assertNotContains(r, "/governance/")

    @override_settings(SITE_ORG_SLUG="jreas")
    def test_org_home_without_published_campaign(self):
        r = self.client.get("/")
        self.assertContains(r, "Giving opens soon")
        self.assertNotContains(r, "JREAS Hub")

    @override_settings(SITE_ORG_SLUG="jreas")
    def test_governance_page_only_when_set(self):
        self.assertEqual(self.client.get("/governance/").status_code, 404)
        self.org.governance_text = "Members vote."; self.org.governance_url = "https://dash.workers.vc/o/jreas/about/"
        self.org.save()
        r = self.client.get("/governance/")
        self.assertContains(r, "Members vote.")
        self.assertContains(r, "https://dash.workers.vc/o/jreas/about/")
        self.assertContains(self.client.get("/"), "/governance/")

    @override_settings(SITE_ORG_SLUG="missing")
    def test_misconfigured_site_org_is_loud(self):
        from django.core.exceptions import ImproperlyConfigured
        with self.assertRaises(ImproperlyConfigured):
            self.client.get("/")

    @override_settings(SITE_ORG_SLUG="jreas")
    def test_updates_stream_and_feed(self):
        import datetime
        from blog.models import Post
        c = Campaign.objects.get(slug="jreas")
        Campaign.objects.filter(pk=c.pk).update(status="published")
        Update.objects.create(campaign=c, date=datetime.date(2026, 9, 1), text="Receipt: fuel", status="published")
        Update.objects.create(campaign=c, date=datetime.date(2026, 9, 2), text="Draft receipt", status="draft")
        Post.objects.create(org=self.org, slug="p", title="A Post", body="x", status="published")
        r = self.client.get("/updates/")
        self.assertContains(r, "Receipt: fuel")
        self.assertContains(r, "A Post")
        self.assertNotContains(r, "Draft receipt")
        f = self.client.get("/updates/feed/")
        self.assertEqual(f.status_code, 200)
        self.assertContains(f, "Receipt: fuel")
        self.assertContains(f, "A Post")
        self.assertContains(self.client.get("/"), "Receipt: fuel")  # Latest on home


class MultiOrgVolunteerTests(TestCase):
    def test_volunteer_in_two_orgs_sees_both_campaigns(self):
        from django.contrib.auth.models import Permission
        a = Org.objects.create(slug="a", name="Org A")
        b = Org.objects.create(slug="b", name="Org B")
        Org.objects.create(slug="c", name="Org C")
        Campaign.objects.create(org=a, slug="ca", title="Campaign A")
        Campaign.objects.create(org=b, slug="cb", title="Campaign B")
        Campaign.objects.create(org=Org.objects.get(slug="c"), slug="cc", title="Campaign C")
        vol = User.objects.create_user("vol", "v@example.org", "pw", is_staff=True)
        vol.user_permissions.add(*Permission.objects.filter(content_type__app_label="campaigns"))
        VolunteerProfile.objects.create(user=vol, org=a)
        VolunteerProfile.objects.create(user=vol, org=b)
        self.client.login(username="vol", password="pw")
        r = self.client.get("/admin/campaigns/campaign/")
        self.assertContains(r, "Campaign A")
        self.assertContains(r, "Campaign B")
        self.assertNotContains(r, "Campaign C")
