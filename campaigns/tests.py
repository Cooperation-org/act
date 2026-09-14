from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings

from .models import Campaign, Org, ShareLink, Update, VolunteerProfile


class ActTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_jreas")
        cls.campaign = Campaign.objects.get(slug="jreas-coop")

    def test_draft_campaign_hidden_from_public(self):
        self.assertEqual(self.client.get("/c/jreas-coop/").status_code, 404)
        self.assertNotContains(self.client.get("/"), "JREAS")

    def test_published_campaign_renders_with_disclosure_and_ctas(self):
        Campaign.objects.filter(pk=self.campaign.pk).update(status="published")
        r = self.client.get("/c/jreas-coop/")
        self.assertContains(r, "JREAS Hub")
        self.assertContains(r, "complete discretion and control")  # locked language present
        self.assertContains(r, "Mentor")
        self.assertContains(r, "Add your testimony")

    def test_respond_creates_response(self):
        Campaign.objects.filter(pk=self.campaign.pk).update(status="published")
        cta = self.campaign.ctas.get(kind="mentor")
        r = self.client.post(f"/c/jreas-coop/act/{cta.id}/",
                             {"name": "A Person", "email": "a@example.org", "message": "hi"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(cta.responses.count(), 1)

    def test_testimony_requires_login_and_lands_pending(self):
        Campaign.objects.filter(pk=self.campaign.pk).update(status="published")
        r = self.client.get("/c/jreas-coop/testimony/")
        self.assertEqual(r.status_code, 302)  # to login
        User.objects.create_user("vol", password="x")
        self.client.login(username="vol", password="x")
        self.client.post("/c/jreas-coop/testimony/", {"quote": "I know this is real.", "show_identity": ""})
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
        r = self.client.get("/c/jreas-coop/card/story.png")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "image/png")


class SiteModeTests(TestCase):
    """ACT_SITE_ORG=jreas-coop: the deployment is the Jreas Coop site."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_jreas")
        cls.org = Campaign.objects.get(slug="jreas-coop").org
        cls.other = Org.objects.create(slug="other", name="Other Org")
        Campaign.objects.create(org=cls.other, slug="elsewhere", title="Elsewhere Campaign", status="published")

    def test_platform_mode_lists_every_org(self):
        Campaign.objects.filter(slug="jreas-coop").update(status="published")
        r = self.client.get("/")
        self.assertContains(r, "JREAS Hub")
        self.assertContains(r, "Elsewhere Campaign")

    @override_settings(SITE_ORG_SLUG="jreas-coop")
    def test_org_home_brands_and_scopes(self):
        self.org.tagline = "A tagline"; self.org.about = "About **us**"; self.org.save()
        Campaign.objects.filter(slug="jreas-coop").update(status="published")
        r = self.client.get("/")
        self.assertContains(r, "Jreas Coop")
        self.assertContains(r, "A tagline")
        self.assertContains(r, "About <strong>us</strong>")
        self.assertContains(r, "JREAS Hub")
        self.assertNotContains(r, "Elsewhere Campaign")
        self.assertContains(r, "complete discretion and control")
        self.assertContains(r, 'href="/c/jreas-coop/"')  # Give in nav
        self.assertEqual(self.client.get("/c/elsewhere/").status_code, 404)
        self.assertNotContains(r, "/governance/")

    @override_settings(SITE_ORG_SLUG="jreas-coop")
    def test_org_home_without_published_campaign(self):
        r = self.client.get("/")
        self.assertContains(r, "Giving opens soon")
        self.assertNotContains(r, "JREAS Hub")

    @override_settings(SITE_ORG_SLUG="jreas-coop")
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

    @override_settings(SITE_ORG_SLUG="jreas-coop")
    def test_updates_stream_and_feed(self):
        import datetime
        from blog.models import Post
        c = Campaign.objects.get(slug="jreas-coop")
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


class PreviewTests(TestCase):
    def test_draft_campaign_opens_with_preview_token_only(self):
        call_command("seed_jreas")
        c = Campaign.objects.get(slug="jreas-coop")
        self.assertEqual(self.client.get("/c/jreas-coop/").status_code, 404)
        self.assertEqual(self.client.get("/c/jreas-coop/?preview=wrong").status_code, 404)
        r = self.client.get(c.preview_path)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "DRAFT")

    def test_draft_post_opens_with_preview_token_only(self):
        from blog.models import Post
        org = Org.objects.create(slug="o", name="O")
        p = Post.objects.create(org=org, slug="d", title="Draft Post", body="x")
        self.assertEqual(self.client.get("/blog/d/").status_code, 404)
        self.assertEqual(self.client.get("/blog/d/?preview=nope").status_code, 404)
        self.assertContains(self.client.get(p.preview_path), "Draft Post")


class LinkedClaimTests(TestCase):
    def setUp(self):
        call_command("seed_jreas")
        self.c = Campaign.objects.get(slug="jreas-coop")
        Campaign.objects.filter(pk=self.c.pk).update(status="published")
        User.objects.create_superuser("admin", "a@example.org", "pw")
        self.client.login(username="admin", password="pw")

    def test_testimony_form_carries_recorder_and_video_url(self):
        r = self.client.get("/c/jreas-coop/testimony/")
        self.assertContains(r, "<linked-video-recorder")
        self.assertContains(r, "video-recorder.js")
        self.client.post("/c/jreas-coop/testimony/", {"quote": "Seen it.", "video_url": "https://f.example/v.webm"})
        t = self.c.testimonials.get()
        self.assertEqual(t.video_url, "https://f.example/v.webm")
        self.assertEqual(t.status, "pending")

    def test_publish_without_credentials_refuses_to_sign_but_publishes(self):
        from .models import Testimonial
        t = Testimonial.objects.create(campaign=self.c, quote="q")
        self.client.post("/admin/campaigns/testimonial/", {"action": "sign_and_publish", "_selected_action": [t.pk]})
        t.refresh_from_db()
        self.assertEqual(t.status, "published")
        self.assertIsNone(t.claim_id)
        self.assertIn("LT_CLIENT_ID", t.sign_error)
        self.assertContains(self.client.get("/c/jreas-coop/"), "not yet signed")

    @override_settings(LT_CLIENT_ID="id", LT_CLIENT_SECRET="s", LT_API="https://lt.example", PUBLIC_URL="https://act.example")
    def test_sign_posts_claim_and_renders_badge(self):
        from unittest.mock import patch, MagicMock
        from .models import Testimonial
        t = Testimonial.objects.create(campaign=self.c, quote="I know this is real.", video_url="https://f.example/v.webm",
                                       display_name="Secret Name", show_identity=False)
        resp = MagicMock(ok=True, status_code=201); resp.json.return_value = {"id": 4242}
        with patch("campaigns.linkedtrust.requests.post", return_value=resp) as post:
            self.client.post("/admin/campaigns/testimonial/", {"action": "sign_and_publish", "_selected_action": [t.pk]})
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://lt.example/api/claims")
        self.assertEqual(kwargs["headers"]["x-lt-client-id"], "id")
        body = kwargs["json"]
        self.assertEqual(body["subject"], "https://act.example/c/jreas-coop/")
        self.assertEqual(body["claim"], "ENDORSES")
        self.assertEqual(body["videoUrl"], "https://f.example/v.webm")
        self.assertNotIn("name", body)  # identity not opted in
        t.refresh_from_db()
        self.assertEqual(t.claim_id, 4242)
        self.assertEqual(t.linkedclaim_uri, "https://lt.example/claims/4242")
        self.assertEqual(t.status, "published")
        self.assertContains(self.client.get("/c/jreas-coop/"), '<linked-badge claim-id="4242"')


class BasePathTests(TestCase):
    """Served under a prefix (demos.linkedtrust.us/act/), every link must carry it.
    In production gunicorn sets the script prefix from SCRIPT_NAME; here we set it directly."""

    @override_settings(STATIC_URL="/act/static/")
    def test_links_carry_prefix(self):
        import re
        from django.test.utils import override_script_prefix
        call_command("seed_jreas")
        Campaign.objects.filter(slug="jreas-coop").update(status="published")
        with override_script_prefix("/act/"):
            for path in ["/", "/c/jreas-coop/", "/updates/", "/blog/", "/people/", "/c/jreas-coop/testimony/"]:
                r = self.client.get(path, SCRIPT_NAME="/act")
                if r.status_code == 302:
                    self.assertTrue(r["Location"].startswith("/act/admin/login/"), r["Location"])
                    continue
                self.assertEqual(r.status_code, 200, path)
                bad = [h for h in re.findall(r'(?:href|src|action)="(/[^"]*)"', r.content.decode())
                       if not h.startswith("/act/")]
                self.assertEqual(bad, [], f"{path}: links without /act prefix")
