from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from campaigns.models import Org
from people.models import Person

from .models import Post


class BlogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Org.objects.create(slug="jreas", name="Jreas Coop")
        cls.author = Person.objects.create(org=cls.org, slug="s", name="S Author", status="published",
                                           consent_on_record=True)

    def test_draft_hidden_published_shown_with_markdown_and_author(self):
        Post.objects.create(org=self.org, slug="d", title="Draft Post", body="x")
        p = Post.objects.create(org=self.org, slug="p", title="Live Post", body="**bold** words",
                                author=self.author, status="published")
        self.assertIsNotNone(p.published_at)
        r = self.client.get("/blog/")
        self.assertContains(r, "Live Post")
        self.assertNotContains(r, "Draft Post")
        r = self.client.get("/blog/p/")
        self.assertContains(r, "<strong>bold</strong>")
        self.assertContains(r, "/people/s/")
        self.assertEqual(self.client.get("/blog/d/").status_code, 404)

    def test_author_hidden_when_profile_unpublished(self):
        self.author.status = "draft"; self.author.save()
        Post.objects.create(org=self.org, slug="p", title="Live", body="x", author=self.author, status="published")
        self.assertNotContains(self.client.get("/blog/p/"), "S Author")

    def test_feed(self):
        Post.objects.create(org=self.org, slug="p", title="Feed Post", body="x", status="published")
        r = self.client.get("/blog/feed/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Feed Post")
        self.assertContains(r, "/blog/p/")

    def test_admin_publish_sets_published_at(self):
        User.objects.create_superuser("admin", "a@example.org", "pw")
        p = Post.objects.create(org=self.org, slug="p", title="T", body="x")
        self.client.login(username="admin", password="pw")
        self.client.post("/admin/blog/post/", {"action": "publish_posts", "_selected_action": [p.pk]})
        p.refresh_from_db()
        self.assertEqual(p.status, "published")
        self.assertIsNotNone(p.published_at)

    @override_settings(SITE_ORG_SLUG="jreas")
    def test_site_mode_scopes_posts(self):
        other = Org.objects.create(slug="other", name="Other")
        Post.objects.create(org=other, slug="o", title="Other Post", body="x", status="published")
        Post.objects.create(org=self.org, slug="m", title="My Post", body="x", status="published")
        r = self.client.get("/blog/")
        self.assertContains(r, "My Post")
        self.assertNotContains(r, "Other Post")
