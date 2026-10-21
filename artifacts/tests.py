import io
import json
import shutil
import tempfile
from email.message import EmailMessage
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from campaigns.models import Org, VolunteerProfile

from .intake import add_file, kind_for
from .models import Artifact, ArtifactKind, Collection, Source, SourceKind, Story, StoryBlock
from .spouts import drive_folder_id, file_email, import_claims, parse_email

MEDIA = tempfile.mkdtemp()


def png_bytes(color="red"):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, "PNG")
    return buf.getvalue()


@override_settings(MEDIA_ROOT=MEDIA)
class Base(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        User = get_user_model()
        self.org = Org.objects.create(slug="o1", name="Org One", website="https://org-one.example")
        self.other = Org.objects.create(slug="o2", name="Org Two")
        self.user = User.objects.create_user("lead", "lead@example.org", "pw-123456789")
        VolunteerProfile.objects.create(user=self.user, org=self.org, is_approver=True)
        self.upload = Source.objects.create(org=self.org, kind=SourceKind.UPLOAD)


class IntakeTests(Base):
    def test_same_file_twice_is_one_artifact(self):
        data = png_bytes()
        a, created = add_file(self.org, data=data, filename="a.png", source=self.upload, words="first")
        b, created2 = add_file(self.org, data=data, filename="b.png", source=self.upload, words="second")
        self.assertTrue(created)
        self.assertFalse(created2)
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(Artifact.objects.filter(org=self.org).count(), 1)
        a.refresh_from_db()
        self.assertIn("first", a.words)
        self.assertIn("second", a.words)

    def test_same_file_in_two_orgs_is_two_artifacts(self):
        data = png_bytes()
        add_file(self.org, data=data, filename="a.png")
        add_file(self.other, data=data, filename="a.png")
        self.assertEqual(Artifact.objects.count(), 2)

    def test_kind_from_type(self):
        self.assertEqual(kind_for("image/jpeg"), ArtifactKind.PHOTO)
        self.assertEqual(kind_for("video/mp4"), ArtifactKind.VIDEO)
        self.assertEqual(kind_for("audio/m4a"), ArtifactKind.VOICE)
        self.assertEqual(kind_for("", "report.pdf"), ArtifactKind.FILE)

    def test_words_kept_verbatim(self):
        words = "  She opened the shop on Main St.\n\nFirst day: 40 customers!  "
        a, _ = add_file(self.org, data=b"x", filename="n.txt", words=words)
        self.assertEqual(a.words, words)


class EmailTests(Base):
    def _mail(self, with_photo=True):
        m = EmailMessage()
        m["From"] = "Program Lead <lead@example.org>"
        m["To"] = "stories-o1@intake.example"
        m["Subject"] = "Opening day"
        m["Date"] = "Tue, 13 Oct 2026 10:00:00 +0000"
        m["Message-ID"] = "<abc@example.org>"
        m.set_content("Line out the door.")
        if with_photo:
            m.add_attachment(png_bytes("blue"), maintype="image", subtype="png", filename="door.png")
        return m.as_bytes()

    def test_parse(self):
        p = parse_email(self._mail())
        self.assertEqual(p["sender"], "lead@example.org")
        self.assertEqual(p["message_id"], "<abc@example.org>")
        self.assertEqual(p["words"], "Line out the door.")
        self.assertEqual(len(p["files"]), 1)
        self.assertEqual(p["files"][0][0], "door.png")

    def test_file_email_makes_photo_artifact_with_words(self):
        src = Source.objects.create(org=self.org, kind=SourceKind.EMAIL, config={"address": "stories-o1@intake.example"})
        made = file_email(src, parse_email(self._mail()))
        a = made[0][0]
        self.assertEqual(a.kind, ArtifactKind.PHOTO)
        self.assertEqual(a.sent_by, "lead@example.org")
        self.assertIn("Opening day", a.words)
        self.assertIn("Line out the door.", a.words)
        self.assertEqual(a.happened_at.year, 2026)
        # the same email again changes nothing
        file_email(src, parse_email(self._mail()))
        self.assertEqual(Artifact.objects.filter(org=self.org).count(), 1)

    def test_email_without_files_is_a_text_artifact(self):
        src = Source.objects.create(org=self.org, kind=SourceKind.EMAIL)
        made = file_email(src, parse_email(self._mail(with_photo=False)))
        self.assertEqual(made[0][0].kind, ArtifactKind.TEXT)


class LinkedClaimsTests(Base):
    def test_import_claims(self):
        src = Source.objects.create(org=self.org, kind=SourceKind.LINKEDCLAIMS,
                                    config={"subject": "https://org-one.example"})
        body = {"claims": [{"id": 7, "claim": "ENDORSES", "statement": "They helped me start.",
                            "sourceURI": "https://example.org/u/1", "effectiveDate": "2026-09-21T00:00:00.000Z"}],
                "pagination": {"totalPages": 1}}
        resp = mock.Mock(status_code=200, json=lambda: body)
        with mock.patch("artifacts.spouts.requests.get", return_value=resp) as get:
            self.assertEqual(import_claims(src), (1, 0))
            self.assertEqual(import_claims(src), (0, 1))
        self.assertIn("/api/claims/subject/https://org-one.example", get.call_args[0][0])
        a = Artifact.objects.get(org=self.org, kind=ArtifactKind.CLAIM)
        self.assertEqual(a.claim_id, 7)
        self.assertEqual(a.words, "They helped me start.")
        self.assertTrue(a.source_url.endswith("/claims/7"))


class DriveTests(TestCase):
    def test_folder_id_from_link(self):
        self.assertEqual(drive_folder_id("https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp?usp=sharing"),
                         "1AbCdEfGhIjKlMnOp")
        self.assertEqual(drive_folder_id("https://drive.google.com/open?id=1AbCdEfGhIjKlMnOp"), "1AbCdEfGhIjKlMnOp")
        self.assertEqual(drive_folder_id("not a link"), "")


class DriveImportTests(Base):
    PAGE = ('<div class="flip-list"><div class="flip-entry" id="entry-F1"><div class="flip-entry-info">'
            '<a href="https://drive.google.com/file/d/FILEID00001/view?usp=drive_web" target="_blank">'
            '<div class="flip-entry-list-icon"><img src="https://drive-thirdparty.googleusercontent.com/16/type/image/png" alt=""/></div>'
            '<div class="flip-entry-title">shop &amp; sign.png</div></a></div></div>'
            '<div class="flip-entry" id="entry-D1"><div class="flip-entry-info">'
            '<a href="https://docs.google.com/document/d/DOCID000001/edit" target="_blank">'
            '<div class="flip-entry-title">A Google Doc</div></a></div></div></div>')

    def test_public_folder_import_copies_files_once(self):
        src = Source.objects.create(org=self.org, kind=SourceKind.DRIVE, config={"folder_id": "FOLDER00001"})
        listing = mock.Mock(status_code=200, text=self.PAGE)
        file = mock.Mock(status_code=200, content=png_bytes("green"), headers={"Content-Type": "image/png"})
        with override_settings(ACT_GOOGLE_API_KEY=""), \
                mock.patch("artifacts.spouts.requests.get", side_effect=[listing, file, listing]):
            from .spouts import import_drive
            self.assertEqual(import_drive(src), (1, 0))
            self.assertEqual(import_drive(src), (0, 1))
        a = Artifact.objects.get(org=self.org)
        self.assertEqual(a.filename, "shop & sign.png")
        self.assertEqual(a.kind, ArtifactKind.PHOTO)
        self.assertEqual(a.source_ref, "FILEID00001")


class PageTests(Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_signed_out_goes_to_signin(self):
        self.client.logout()
        r = self.client.get(reverse("artifacts:library"))
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("artifacts:signin"), r["Location"])

    def test_start_creates_org_and_ways(self):
        User = get_user_model()
        u = User.objects.create_user("new", "new@example.org", "pw-123456789")
        self.client.force_login(u)
        self.assertRedirects(self.client.get(reverse("artifacts:library")), reverse("artifacts:start"))
        r = self.client.post(reverse("artifacts:start"), {"name": "Entrepreneurs Together", "website": "et.example"})
        self.assertRedirects(r, reverse("artifacts:ways"))
        org = Org.objects.get(name="Entrepreneurs Together")
        self.assertEqual(org.website, "https://et.example")
        self.assertTrue(VolunteerProfile.objects.filter(user=u, org=org, is_approver=True).exists())
        self.assertTrue(Source.objects.filter(org=org, kind=SourceKind.EMAIL).exists())
        page = self.client.get(reverse("artifacts:ways")).content.decode()
        self.assertIn("Your email address is being set up", page)

    def test_upload_then_library(self):
        f = SimpleUploadedFile("shop.png", png_bytes(), content_type="image/png")
        self.client.post(reverse("artifacts:send"), {"files": [f], "words": "New shop", "happened_on": "2026-10-01"})
        a = Artifact.objects.get(org=self.org)
        self.assertEqual(a.kind, ArtifactKind.PHOTO)
        self.assertEqual(a.added_by, self.user)
        self.assertEqual(a.happened_at.date().isoformat(), "2026-10-01")
        page = self.client.get(reverse("artifacts:library")).content.decode()
        self.assertIn("New shop", page)

    def test_other_orgs_artifacts_are_invisible(self):
        theirs, _ = add_file(self.other, data=b"theirs", filename="t.txt", words="secret")
        self.assertEqual(self.client.get(reverse("artifacts:artifact", args=[theirs.pk])).status_code, 404)
        self.assertNotIn("secret", self.client.get(reverse("artifacts:library")).content.decode())

    def test_collection_story_publish(self):
        a, _ = add_file(self.org, data=png_bytes(), filename="a.png", words="one")
        b, _ = add_file(self.org, data=b"note", filename="b.txt", words="two")
        r = self.client.post(reverse("artifacts:library"), {"ids": [a.pk, b.pk], "action": "collect", "name": "Cohort"})
        c = Collection.objects.get(org=self.org, name="Cohort")
        self.assertRedirects(r, reverse("artifacts:collection", args=[c.pk]))
        r = self.client.post(reverse("artifacts:collection", args=[c.pk]), {"action": "story"})
        s = Story.objects.get(org=self.org)
        self.assertEqual(s.blocks.count(), 2)
        # draft is not public
        self.client.logout()
        self.assertEqual(self.client.get(reverse("artifacts:story", args=[s.slug])).status_code, 404)
        self.client.force_login(self.user)
        blocks = json.dumps([{"artifact": b.pk, "text": "Then this."}, {"artifact": a.pk, "text": ""},
                             {"artifact": None, "text": "The end."}])
        self.client.post(reverse("artifacts:story_edit", args=[s.slug]),
                         {"title": "Our year", "statement": "It worked.", "blocks": blocks, "action": "publish"})
        s.refresh_from_db()
        self.assertEqual(s.status, "published")
        self.assertEqual([x.artifact_id for x in s.blocks.all()], [b.pk, a.pk, None])
        self.client.logout()
        page = self.client.get(reverse("artifacts:story", args=[s.slug])).content.decode()
        self.assertIn("Our year", page)
        self.assertIn("The end.", page)

    def test_story_edit_ignores_other_orgs_artifacts(self):
        theirs, _ = add_file(self.other, data=b"theirs", filename="t.txt")
        s = Story.objects.create(org=self.org, title="T", slug="t")
        self.client.post(reverse("artifacts:story_edit", args=[s.slug]),
                         {"title": "T", "blocks": json.dumps([{"artifact": theirs.pk, "text": ""}])})
        self.assertEqual(StoryBlock.objects.filter(story=s).count(), 0)

    def test_manifest_has_share_target(self):
        m = json.loads(self.client.get(reverse("artifacts:manifest")).content)
        self.assertEqual(m["share_target"]["action"], "/share/")
        self.assertEqual(m["share_target"]["method"], "POST")

    def test_share_target_refuses_cross_site_post(self):
        r = self.client.post(reverse("artifacts:share"), {"text": "x"}, HTTP_SEC_FETCH_SITE="cross-site")
        self.assertEqual(r.status_code, 400)


class SortingTests(Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_drive_subfolders_become_labels(self):
        from .spouts import import_drive
        src = Source.objects.create(org=self.org, kind=SourceKind.DRIVE, config={"folder_id": "ROOT0000001"})
        files = [{"id": "F1", "name": "a.png", "mime": "image/png", "size": 0, "url": "", "when": None, "by": "",
                  "folders": ("Lowell", "Demo Day")}]
        got = mock.Mock(status_code=200, headers={"Content-Type": "image/png"}, content=png_bytes())
        with mock.patch("artifacts.spouts._drive_public_files", return_value=iter(files)), \
                mock.patch("artifacts.spouts._drive_download", return_value=got):
            self.assertEqual(import_drive(src), (1, 0))
        a = Artifact.objects.get(org=self.org)
        self.assertEqual(sorted(t.name for t in a.tags.all()), ["demo day", "lowell"])

    def test_filter_needs_every_chosen_label(self):
        from .models import Tag, TagGroup
        a, _ = add_file(self.org, data=png_bytes("red"), filename="a.png")
        b, _ = add_file(self.org, data=png_bytes("blue"), filename="b.png")
        lowell = Tag.objects.create(org=self.org, name="lowell", group=TagGroup.PART)
        pitch = Tag.objects.create(org=self.org, name="pitch")
        a.tags.add(lowell, pitch)
        b.tags.add(pitch)
        r = self.client.get(reverse("artifacts:library"), {"tag": "pitch"})
        self.assertEqual(r.context["total"], 2)
        r = self.client.get(reverse("artifacts:library"), {"tag": "pitch", "part": "lowell"})
        self.assertEqual(r.context["total"], 1)
        self.assertContains(r, "Part of Org One")

    def test_artifact_page_saves_parts_and_labels(self):
        from .models import TagGroup
        a, _ = add_file(self.org, data=png_bytes(), filename="a.png")
        self.client.post(reverse("artifacts:artifact", args=[a.pk]), {"parts": "Lawrence", "tags": "market, food"})
        self.assertEqual({(t.name, t.group) for t in a.tags.all()},
                         {("lawrence", TagGroup.PART), ("market", ""), ("food", "")})

    def test_rename_onto_existing_label_merges(self):
        from .models import Tag
        a, _ = add_file(self.org, data=png_bytes(), filename="a.png")
        x = Tag.objects.create(org=self.org, name="demo-day")
        Tag.objects.create(org=self.org, name="demo day")
        a.tags.add(x)
        self.client.post(reverse("artifacts:labels"), {"pk": x.pk, "action": "save", "name": "demo day"})
        self.assertEqual([t.name for t in a.tags.all()], ["demo day"])

    @override_settings(ANTHROPIC_API_KEY="k")
    def test_ai_sort_labels_and_titles(self):
        from . import ai
        a, _ = add_file(self.org, data=png_bytes(), filename="a.png", words="Opening day in Lowell")
        reply = {"content": [{"type": "text", "text": json.dumps({"items": [
            {"id": a.pk, "title": "Ribbon cutting", "parts": ["Lowell"], "labels": ["opening", "ribbon"]}]})}]}
        with mock.patch("artifacts.ai.requests.post", return_value=mock.Mock(status_code=200, json=lambda: reply)) as post:
            self.assertEqual(ai.sort_artifacts(self.org, [a.pk]), 1)
        sent = post.call_args.kwargs["json"]["messages"][0]["content"]
        self.assertTrue(any(c["type"] == "image" for c in sent))
        a.refresh_from_db()
        self.assertEqual(a.title, "Ribbon cutting")
        self.assertEqual({(t.name, t.group) for t in a.tags.all()}, {("lowell", "part"), ("opening", ""), ("ribbon", "")})
        self.assertEqual(len(a.ai_labels), 3)

    @override_settings(ANTHROPIC_API_KEY="k", MINIMAX_API_KEY="m")
    def test_falls_back_to_minimax_without_images(self):
        from . import ai
        a, _ = add_file(self.org, data=png_bytes(), filename="a.png", words="Opening day")
        ok = {"content": [{"type": "text", "text": json.dumps({"items": [{"id": a.pk, "title": "t", "labels": ["x"]}]})}]}
        calls = []

        def post(url, **kw):
            calls.append((url, kw["json"]["messages"][0]["content"]))
            if "anthropic.com" in url:
                return mock.Mock(status_code=400, text="credit balance is too low")
            return mock.Mock(status_code=200, json=lambda: ok)
        with mock.patch("artifacts.ai.requests.post", side_effect=post):
            self.assertEqual(ai.sort_artifacts(self.org, [a.pk]), 1)
        self.assertIn("minimax", calls[1][0])
        self.assertTrue(any(c["type"] == "image" for c in calls[0][1]))
        self.assertFalse(any(c["type"] == "image" for c in calls[1][1]))


class DirectEditTests(Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_quick_archive_and_title(self):
        a, _ = add_file(self.org, data=png_bytes(), filename="a.png")
        url = reverse("artifacts:artifact_quick", args=[a.pk])
        self.client.post(url, json.dumps({"status": "hidden", "title": "Ribbon"}), content_type="application/json")
        a.refresh_from_db()
        self.assertEqual((a.status, a.title), ("hidden", "Ribbon"))
        other, _ = add_file(self.other, data=png_bytes("blue"), filename="b.png")
        r = self.client.post(reverse("artifacts:artifact_quick", args=[other.pk]), "{}", content_type="application/json")
        self.assertEqual(r.status_code, 404)

    def test_story_autosave_leaves_no_flash(self):
        a, _ = add_file(self.org, data=png_bytes(), filename="a.png")
        s = Story.objects.create(org=self.org, title="T", slug="t")
        r = self.client.post(reverse("artifacts:story_edit", args=[s.slug]),
                             {"title": "T2", "blocks": json.dumps([{"artifact": a.pk, "text": "hi"}]), "action": "save"},
                             HTTP_ACCEPT="application/json")
        self.assertEqual(r.json(), {"ok": True})
        self.assertEqual(s.blocks.get().text, "hi")
        r = self.client.get(reverse("artifacts:story_edit", args=[s.slug]))
        self.assertNotContains(r, "Saved.")


class TemplateTests(Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        from datetime import datetime, timezone as tz
        self.a, _ = add_file(self.org, data=png_bytes("red"), filename="a.png",
                             happened_at=datetime(2026, 8, 3, tzinfo=tz.utc))
        self.b, _ = add_file(self.org, data=png_bytes("blue"), filename="b.png",
                             happened_at=datetime(2026, 9, 9, tzinfo=tz.utc))

    def test_timeline_from_dates_in_date_order_with_months(self):
        r = self.client.post(reverse("artifacts:stories"), {"template": "timeline", "since": "2026-08-01",
                                                            "until": "2026-09-30"})
        s = Story.objects.get(org=self.org)
        self.assertRedirects(r, reverse("artifacts:story_edit", args=[s.slug]), fetch_redirect_response=False)
        self.assertEqual((s.template, [b.artifact_id for b in s.blocks.all()]), ("timeline", [self.a.pk, self.b.pk]))
        self.assertEqual(s.title, "Org One, August 2026 to September 2026")
        page = self.client.get(reverse("artifacts:story", args=[s.slug]))
        self.assertContains(page, '<h2 class="month">August 2026</h2>')
        self.assertContains(page, '<h2 class="month">September 2026</h2>')

    def test_timeline_dates_narrow_it(self):
        self.client.post(reverse("artifacts:stories"), {"template": "timeline", "since": "2026-09-01"})
        self.assertEqual([b.artifact_id for b in Story.objects.get().blocks.all()], [self.b.pk])

    def test_one_page_for_a_label(self):
        from .models import Tag, TagGroup
        t = Tag.objects.create(org=self.org, name="lowell", group=TagGroup.PART)
        self.a.tags.add(t)
        self.client.post(reverse("artifacts:stories"), {"template": "onepage", "label": "part:lowell"})
        s = Story.objects.get()
        self.assertEqual((s.template, s.title, [b.artifact_id for b in s.blocks.all()]), ("onepage", "Lowell", [self.a.pk]))
        self.assertContains(self.client.get(reverse("artifacts:story", args=[s.slug])), 'class="pieces"')


class QRTests(Base):
    def test_ways_page_has_qr_to_send(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse("artifacts:ways"))
        self.assertContains(r, "<svg")
        self.assertContains(r, "Scan with your phone")


class SlugTests(Base):
    def test_long_title_slug_cuts_at_a_word_and_fits(self):
        from .views import _unique_slug
        slug = _unique_slug(Story, "umbrella-collective Riverside Bike Repair, April 2026 to October 2026")
        self.assertEqual(slug, "umbrella-collective-riverside-bike-repair")
        self.assertLessEqual(len(slug), 50)
