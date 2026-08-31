from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from .models import Campaign, ShareLink


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
