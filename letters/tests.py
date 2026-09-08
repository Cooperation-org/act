import base64
import io
import tempfile
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.utils import timezone
from django.test import TestCase, override_settings
from PIL import Image

from campaigns.models import Org

from .mail import retry_due
from .models import RETRY_DELAYS, EmailLog, Letter, Signature, SignatureField


def png_data_url():
    buf = io.BytesIO()
    Image.new("RGBA", (300, 100), (0, 0, 0, 0)).save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class LetterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        org = Org.objects.create(slug="coop", name="Cooperation.org")
        cls.letter = Letter.objects.create(org=org, slug="test", title="A Letter", status="published",
                                           body="We **affirm** this.\n\n- one\n- two")
        SignatureField.objects.create(letter=cls.letter, key="affiliation", label="Affiliation", sort=1)
        SignatureField.objects.create(letter=cls.letter, key="commentary", label="Commentary", kind="long", sort=2)
        cls.base = {"first_name": "Ada", "last_name": "Lovelace", "city": "Tucson", "country": "USA"}

    def test_draft_hidden(self):
        Letter.objects.filter(pk=self.letter.pk).update(status="draft")
        self.assertEqual(self.client.get("/letters/test/").status_code, 404)

    def test_page_renders_markdown_fields_and_count(self):
        r = self.client.get("/letters/test/")
        self.assertContains(r, "<strong>affirm</strong>")
        self.assertContains(r, "<li>one</li>")
        self.assertContains(r, "Affiliation")
        self.assertContains(r, "Commentary")
        self.assertContains(r, "<b>0</b>")

    def test_needs_email_or_drawing(self):
        r = self.client.post("/letters/test/", self.base)
        self.assertContains(r, "Enter your email or draw your signature")
        self.assertEqual(Signature.objects.count(), 0)

    @override_settings(EMAIL_HOST="", DEFAULT_FROM_EMAIL="")
    def test_email_only_waits_for_confirmation_and_logs_mail_failure(self):
        r = self.client.post("/letters/test/", {**self.base, "email": "Ada@Example.org",
                                                "extra_affiliation": "Analytical Engine Society"})
        s = Signature.objects.get()
        self.assertEqual(s.email, "ada@example.org")
        self.assertEqual(s.extras, {"affiliation": "Analytical Engine Society"})
        self.assertFalse(s.is_valid)
        self.assertEqual(self.letter.count(), 0)
        self.assertContains(r, "could not be sent")
        self.assertIn("not configured", EmailLog.objects.get().error)
        self.assertEqual(s.confirmation, "retrying")
        self.assertEqual(s.confirm_attempts, 1)
        self.assertAlmostEqual(s.confirm_next_retry_at, s.confirm_last_attempt_at + timedelta(hours=1),
                               delta=timedelta(seconds=5))

    @override_settings(EMAIL_HOST="smtp.example.org", DEFAULT_FROM_EMAIL="letters@example.org",
                       EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_confirm_link_makes_signature_count(self):
        r = self.client.post("/letters/test/", {**self.base, "email": "ada@example.org", "keep_updated": "on"})
        self.assertContains(r, "Check your email")
        self.assertEqual(len(mail.outbox), 1)
        s = Signature.objects.get()
        self.assertEqual(s.confirmation, "waiting")
        self.assertIn(f"/letters/test/confirm/{s.token}/", mail.outbox[0].body)
        r = self.client.get(f"/letters/test/confirm/{s.token}/")
        self.assertContains(r, "Your signature is on the letter")
        s.refresh_from_db()
        self.assertTrue(s.is_valid)
        self.assertTrue(s.keep_updated)
        self.assertEqual(self.letter.count(), 1)
        self.assertContains(self.client.get("/letters/test/"), "Ada Lovelace")

    def test_drawn_signature_counts_immediately(self):
        r = self.client.post("/letters/test/", {**self.base, "drawn": png_data_url(),
                                                "extra_commentary": "Count me in.\nTwice."})
        self.assertContains(r, "Your signature is on the letter")
        s = Signature.objects.get()
        self.assertTrue(s.drawn.name.endswith(".png"))
        self.assertEqual(s.confirmation, "drawn")
        self.assertEqual(self.letter.count(), 1)
        page = self.client.get("/letters/test/")
        self.assertContains(page, "Count me in.<br>Twice.")
        self.assertContains(page, s.drawn.url)

    def test_bad_drawing_rejected(self):
        r = self.client.post("/letters/test/", {**self.base, "drawn": "data:image/png;base64,AAAA"})
        self.assertContains(r, "could not be read")

    def test_duplicate_confirmed_email(self):
        Signature.objects.create(letter=self.letter, email="ada@example.org", first_name="A", last_name="L",
                                 city="x", country="y", confirmed_at="2026-01-01T00:00Z")
        r = self.client.post("/letters/test/", {**self.base, "email": "ada@example.org"})
        self.assertContains(r, "already signed")
        self.assertEqual(Signature.objects.count(), 1)

    def test_pdf(self):
        self.client.post("/letters/test/", {**self.base, "drawn": png_data_url()})
        r = self.client.get("/letters/test.pdf")
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertTrue(r.content.startswith(b"%PDF"))

    def test_hidden_signature_not_shown(self):
        self.client.post("/letters/test/", {**self.base, "drawn": png_data_url()})
        Signature.objects.update(hidden=True)
        self.assertEqual(self.letter.count(), 0)
        self.assertNotContains(self.client.get("/letters/test/"), "Ada Lovelace")

    def test_admin_csv_and_pdf_actions(self):
        from django.contrib.auth.models import User
        User.objects.create_superuser("admin", "a@example.org", "pw")
        self.client.login(username="admin", password="pw")
        self.client.post("/letters/test/", {**self.base, "drawn": png_data_url(), "extra_affiliation": "AES"})
        sig = Signature.objects.get()
        r = self.client.post("/admin/letters/signature/", {"action": "export_csv", "_selected_action": [sig.pk]})
        self.assertEqual(r["Content-Type"], "text/csv")
        self.assertIn(b"Ada,Lovelace,,Tucson,USA,False,drawn", r.content)
        self.assertIn(b"AES", r.content)
        r = self.client.post("/admin/letters/signature/", {"action": "export_pdf", "_selected_action": [sig.pk]})
        self.assertEqual(r["Content-Type"], "application/pdf")
        r = self.client.get("/admin/letters/signature/?confirmation=drawn")
        self.assertContains(r, "Ada")

    def test_index_redirects_single_letter(self):
        self.assertRedirects(self.client.get("/letters/"), "/letters/test/")

    def test_admin_approval_makes_signature_count(self):
        self.client.post("/letters/test/", {**self.base, "email": "ada@example.org"})
        s = Signature.objects.get()
        self.assertEqual(self.letter.count(), 0)
        s.approved = True
        s.save()
        self.assertTrue(s.is_valid)
        self.assertEqual(s.confirmation, "approved")
        self.assertEqual(self.letter.count(), 1)
        self.assertContains(self.client.get("/letters/test/"), "Ada Lovelace")
        self.assertEqual(self.letter.signatures.for_pdf().count(), 1)

    @override_settings(EMAIL_HOST="smtp.example.org", DEFAULT_FROM_EMAIL="letters@example.org",
                       EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_failed_mail_is_retried_with_backoff_then_gives_up(self):
        boom = mock.patch("letters.mail.send_mail", side_effect=OSError("smtp down"))
        with boom:
            self.client.post("/letters/test/", {**self.base, "email": "ada@example.org"})
        s = Signature.objects.get()
        self.assertEqual(s.confirmation, "retrying")
        self.assertIn("smtp down", s.confirm_last_error)
        # not due yet: nothing happens
        self.assertEqual(retry_due(), (0, 0))
        # each retry fails: schedule follows RETRY_DELAYS, then gives up
        for i, delay in enumerate(RETRY_DELAYS):
            s.refresh_from_db()
            self.assertAlmostEqual(s.confirm_next_retry_at, s.confirm_last_attempt_at + delay,
                                   delta=timedelta(seconds=5))
            with boom:
                self.assertEqual(retry_due(now=s.confirm_next_retry_at), (0, 1))
        s.refresh_from_db()
        self.assertEqual(s.confirm_attempts, 1 + len(RETRY_DELAYS))
        self.assertIsNone(s.confirm_next_retry_at)
        self.assertEqual(s.confirmation, "gave up")
        self.assertEqual(EmailLog.objects.filter(signature=s).count(), 1 + len(RETRY_DELAYS))
        self.assertEqual(retry_due(now=timezone.now() + timedelta(days=30)), (0, 0))

    @override_settings(EMAIL_HOST="smtp.example.org", DEFAULT_FROM_EMAIL="letters@example.org",
                       EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
                       ACT_PUBLIC_URL="https://cooperation.org", PUBLIC_URL="https://cooperation.org")
    def test_retry_command_sends_when_mail_recovers(self):
        with mock.patch("letters.mail.send_mail", side_effect=OSError("smtp down")):
            self.client.post("/letters/test/", {**self.base, "email": "ada@example.org"})
        s = Signature.objects.get()
        Signature.objects.filter(pk=s.pk).update(confirm_next_retry_at=timezone.now() - timedelta(minutes=1))
        call_command("retry_confirmations")
        s.refresh_from_db()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(f"https://cooperation.org/letters/test/confirm/{s.token}/", mail.outbox[0].body)
        self.assertEqual(s.confirmation, "waiting")
        self.assertEqual(s.confirm_sends, 1)
        self.assertIsNone(s.confirm_next_retry_at)
        self.assertEqual(s.confirm_last_error, "")
        # confirming after a retry works and stops any further mail
        self.client.get(f"/letters/test/confirm/{s.token}/")
        self.assertEqual(self.letter.count(), 1)
        self.assertEqual(retry_due(now=timezone.now() + timedelta(days=1)), (0, 0))

    def test_letter_text_locks_after_first_signature(self):
        from django.contrib.auth.models import User
        User.objects.create_superuser("admin", "a@example.org", "pw")
        self.client.login(username="admin", password="pw")
        url = f"/admin/letters/letter/{self.letter.pk}/change/"
        r = self.client.get(url)
        self.assertContains(r, 'name="body"')
        self.assertContains(r, "Editable until the first signature")
        self.client.post("/letters/test/", {**self.base, "email": "ada@example.org"})
        r = self.client.get(url)
        self.assertNotContains(r, 'name="body"')
        self.assertContains(r, 'name="fields-0-label"')  # labels stay editable
        self.assertNotContains(r, 'name="fields-0-key"')
        self.assertNotContains(r, 'name="fields-0-DELETE"')
        self.assertContains(r, "Locked")
        # a post that tries to change the text is ignored; status still saves
        r = self.client.post(url, {"status": "draft", "theme": "parchment", "show_signatures": "on",
                                   "updates_label": "Keep me posted", "body": "tampered", "title": "Tampered",
                                   "fields-TOTAL_FORMS": "0", "fields-INITIAL_FORMS": "0", "_save": "Save"})
        self.assertEqual(r.status_code, 302)
        self.letter.refresh_from_db()
        self.assertEqual(self.letter.body, "We **affirm** this.\n\n- one\n- two")
        self.assertEqual(self.letter.title, "A Letter")
        self.assertEqual(self.letter.status, "draft")
        self.assertEqual(self.letter.updates_label, "Keep me posted")
        self.assertEqual(self.letter.fields.count(), 2)

