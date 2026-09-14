from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from campaigns.models import Org, VolunteerProfile

from .models import Person


class PeopleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Org.objects.create(slug="jreas-coop", name="Jreas Coop")
        cls.other = Org.objects.create(slug="other", name="Other")

    def test_only_consented_published_profiles_are_public(self):
        Person.objects.create(org=self.org, slug="a", name="A Person", status="published", consent_on_record=True)
        Person.objects.create(org=self.org, slug="b", name="B Person", status="published", consent_on_record=False)
        Person.objects.create(org=self.org, slug="c", name="C Person", status="draft", consent_on_record=True)
        r = self.client.get("/people/")
        self.assertContains(r, "A Person")
        self.assertNotContains(r, "B Person")
        self.assertNotContains(r, "C Person")
        self.assertEqual(self.client.get("/people/a/").status_code, 200)
        self.assertEqual(self.client.get("/people/b/").status_code, 404)

    def test_admin_publish_refuses_without_consent(self):
        admin = User.objects.create_superuser("admin", "a@example.org", "pw")
        p = Person.objects.create(org=self.org, slug="a", name="A", consent_on_record=False)
        self.client.login(username="admin", password="pw")
        self.client.post("/admin/people/person/", {"action": "publish_people", "_selected_action": [p.pk]})
        p.refresh_from_db()
        self.assertEqual(p.status, "draft")
        p.consent_on_record = True
        p.save()
        self.client.post("/admin/people/person/", {"action": "publish_people", "_selected_action": [p.pk]})
        p.refresh_from_db()
        self.assertEqual(p.status, "published")

    def test_approver_of_one_org_cannot_publish_another(self):
        vol = User.objects.create_user("vol", "v@example.org", "pw", is_staff=True)
        VolunteerProfile.objects.create(user=vol, org=self.org, is_approver=True)
        VolunteerProfile.objects.create(user=vol, org=self.other, is_approver=False)
        from django.contrib.auth.models import Permission
        vol.user_permissions.add(*Permission.objects.filter(content_type__app_label="people"))
        mine = Person.objects.create(org=self.org, slug="m", name="Mine", consent_on_record=True)
        theirs = Person.objects.create(org=self.other, slug="t", name="Theirs", consent_on_record=True)
        self.client.login(username="vol", password="pw")
        r = self.client.get("/admin/people/person/")
        self.assertContains(r, "Mine")
        self.assertContains(r, "Theirs")  # visible: volunteer in both orgs
        self.client.post("/admin/people/person/", {"action": "publish_people", "_selected_action": [mine.pk, theirs.pk]})
        mine.refresh_from_db(); theirs.refresh_from_db()
        self.assertEqual(mine.status, "published")
        self.assertEqual(theirs.status, "draft")

    @override_settings(SITE_ORG_SLUG="jreas-coop")
    def test_site_mode_hides_other_orgs_people(self):
        Person.objects.create(org=self.other, slug="x", name="Elsewhere", status="published", consent_on_record=True)
        self.assertNotContains(self.client.get("/people/"), "Elsewhere")
        self.assertEqual(self.client.get("/people/x/").status_code, 404)
