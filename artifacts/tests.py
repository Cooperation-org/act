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

from .intake import add_file, add_reference, kind_for
from .models import Artifact, ArtifactKind, Collection, Source, SourceKind, Story, StoryBlock, Tag, TagGroup
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


@override_settings(ACT_DEMO_ORG="o1")
class DemoTests(Base):
    def setUp(self):
        super().setUp()
        self.a, _ = add_file(self.org, data=png_bytes(), filename="a.png", source=self.upload, words="sample")
        from .models import Tag
        self.a.tags.add(Tag.objects.create(org=self.org, name="riverside", group="part"))
        s = Story.objects.create(org=self.org, title="S", slug="s", status="published")
        StoryBlock.objects.create(story=s, artifact=self.a, text="t")

    def test_visitor_gets_own_copy_signed_in(self):
        r = self.client.get(reverse("artifacts:demo"))
        self.assertRedirects(r, reverse("artifacts:library") + "?show=inbox", fetch_redirect_response=False)
        from .models import DemoCopy
        d = DemoCopy.objects.get()
        copy = Artifact.objects.get(org=d.org)
        self.assertEqual((copy.file.name, copy.words, [t.name for t in copy.tags.all()]),
                         (self.a.file.name, "sample", ["riverside"]))
        self.assertEqual(Story.objects.get(org=d.org).blocks.get().artifact, copy)
        page = self.client.get(reverse("artifacts:library"))
        self.assertContains(page, "Start your own")
        # an edit in the copy leaves the sample alone
        self.client.post(reverse("artifacts:artifact_quick", args=[copy.pk]), '{"title": "mine"}', content_type="application/json")
        self.a.refresh_from_db()
        self.assertEqual(self.a.title, "")
        # the same visitor coming back gets the same copy
        self.client.get(reverse("artifacts:demo"))
        self.assertEqual(DemoCopy.objects.count(), 1)

    def test_old_copies_removed_but_sample_files_kept(self):
        import os
        from datetime import timedelta
        from django.utils import timezone
        from . import demo
        from .models import DemoCopy
        self.client.get(reverse("artifacts:demo"))
        d = DemoCopy.objects.get()
        own, _ = add_file(d.org, data=png_bytes("blue"), filename="mine.png")
        own_path = own.file.path
        self.assertEqual(demo.remove_old(now=timezone.now() + timedelta(hours=25)), 1)
        self.assertFalse(DemoCopy.objects.exists())
        self.assertFalse(os.path.exists(own_path))
        self.assertFalse(os.path.exists(os.path.dirname(os.path.dirname(os.path.dirname(own_path)))))
        self.assertTrue(os.path.exists(self.a.file.path))
        self.assertTrue(Artifact.objects.filter(pk=self.a.pk).exists())

    def test_start_your_own_signs_guest_out(self):
        self.client.get(reverse("artifacts:demo"))
        r = self.client.get(reverse("artifacts:demo_leave"))
        self.assertIn(reverse("artifacts:signin"), r["Location"])
        self.assertNotIn("_auth_user_id", self.client.session)


class IconTests(TestCase):
    """Icons come from one sprite; a name that is not in it would show as a blank."""

    def _templates(self):
        import pathlib
        return sorted(pathlib.Path(__file__).parent.glob("templates/artifacts/*.html"))

    def test_every_icon_used_is_in_the_sprite(self):
        import re
        sprite = (self._templates()[0].parent / "_icons.html").read_text()
        have = set(re.findall(r'id="i-([a-z-]+)"', sprite))
        used = set()
        for f in self._templates():
            text = f.read_text()
            used |= set(re.findall(r'{% ic "([a-z-]+)"', text))
            used |= set(re.findall(r'href="#i-([a-z-]+)"', text))
        self.assertTrue(used, "no icons found")
        self.assertFalse(used - have, f"icons missing from the sprite: {sorted(used - have)}")

    def test_no_emoji_icons_in_templates(self):
        import re
        emoji = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2705\u2715]")
        for f in self._templates():
            self.assertIsNone(emoji.search(f.read_text()), f"emoji in {f.name}")


class ShellTests(Base):
    """The frame around every page: landmarks, one main, and the right place marked current."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_page_has_skip_link_and_one_main(self):
        page = self.client.get(reverse("artifacts:library")).content.decode()
        self.assertIn('href="#main"', page)
        self.assertEqual(page.count('<main id="main"'), 1)
        self.assertIn("viewport-fit=cover", page)

    def test_top_bar_marks_the_current_place(self):
        for name, label in (("library", "Library"), ("stories", "Stories"), ("collections", "Collections")):
            page = self.client.get(reverse(f"artifacts:{name}")).content.decode()
            nav = page.split('<nav class="topnav"')[1].split("</nav>")[0]
            self.assertEqual(nav.count('aria-current="page"'), 1, name)
            self.assertRegex(nav, rf'aria-current="page">{label}</a>', name)

    def test_phone_has_three_tabs_and_marks_the_current_one(self):
        page = self.client.get(reverse("artifacts:stories")).content.decode()
        tabs = page.split('<nav class="tabs"')[1].split("</nav>")[0]
        self.assertEqual(tabs.count("<a "), 3)
        for label in ("Capture", "My uploads", "Stories"):
            self.assertIn(f"<span>{label}</span>", tabs)
        self.assertRegex(tabs, r'aria-current="page"[^>]*>.*?<span>Stories</span>')
        self.assertEqual(tabs.count('aria-current="page"'), 1)

    def test_everything_else_is_in_the_account_sheet(self):
        page = self.client.get(reverse("artifacts:stories")).content.decode()
        sheet = page.split('<dialog class="sheet" id="account"')[1].split("</dialog>")[0]
        for place in ("send", "library", "stories", "collections", "ways", "labels"):
            self.assertIn(reverse(f"artifacts:{place}"), sheet, place)
        self.assertIn('data-open="account"', page)
        self.assertIn(">LE</button>", page)  # initials of lead@example.org

    def test_inbox_count_sits_beside_inbox(self):
        self.assertNotIn("&middot; 1", self.client.get(reverse("artifacts:stories")).content.decode())
        add_file(self.org, data=png_bytes("pink"), filename="p.png", source=self.upload)
        page = self.client.get(reverse("artifacts:stories")).content.decode()
        self.assertRegex(page, r'Inbox<span class="n">&middot; 1</span>')

    def test_signed_out_welcome_has_no_app_navigation(self):
        self.client.logout()
        page = self.client.get(reverse("artifacts:home")).content.decode()
        self.assertNotIn('class="tabs"', page)
        self.assertNotIn('class="rail"', page)


class SendUploadTests(Base):
    """The page's own script uploads with progress and gets JSON back; a plain form post still redirects."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.ajax = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}

    def test_script_upload_gets_json_and_no_flash(self):
        f = SimpleUploadedFile("a.png", png_bytes("blue"), content_type="image/png")
        r = self.client.post(reverse("artifacts:send"), {"files": [f], "words": "Hello"}, **self.ajax)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["saved"], 1)
        self.assertEqual(data["message"], "Saved 1 item.")
        self.assertEqual(list(r.context["messages"]) if r.context else [], [])
        again = SimpleUploadedFile("a.png", png_bytes("blue"), content_type="image/png")
        r2 = self.client.post(reverse("artifacts:send"), {"files": [again]}, **self.ajax)
        self.assertEqual(r2.json()["saved"], 0)
        self.assertIn("already here", r2.json()["message"])

    def test_script_upload_with_nothing_is_a_plain_error(self):
        r = self.client.post(reverse("artifacts:send"), {}, **self.ajax)
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.json()["ok"])
        self.assertEqual(Artifact.objects.filter(org=self.org).count(), 0)

    def test_note_alone_is_saved(self):
        r = self.client.post(reverse("artifacts:send"), {"words": "Just a thought"}, **self.ajax)
        self.assertEqual(r.json()["message"], "Saved your note.")
        self.assertEqual(Artifact.objects.filter(org=self.org, kind=ArtifactKind.TEXT).count(), 1)

    def test_plain_form_post_still_redirects(self):
        f = SimpleUploadedFile("b.png", png_bytes("green"), content_type="image/png")
        r = self.client.post(reverse("artifacts:send"), {"files": [f]})
        self.assertRedirects(r, reverse("artifacts:send"))

    def test_page_offers_every_way_to_add_and_explains_a_disabled_send(self):
        page = self.client.get(reverse("artifacts:send")).content.decode()
        for needle in ('id="rec-photo"', 'id="rec-video"', 'id="rec-voice"', 'id="open-file"',
                       'data-open="link-sheet"', 'data-open="number-sheet"'):
            self.assertIn(needle, page)
        self.assertIn("What did you see today?", page)
        self.assertIn("Send to inbox", page)
        self.assertNotIn("Click a button", page)

    def test_the_camera_takes_the_screen_as_a_dialog_with_a_way_out(self):
        page = self.client.get(reverse("artifacts:send")).content.decode()
        self.assertRegex(page, r'<dialog id="cam" class="cam" aria-labelledby="cam-mode-text">')
        self.assertIn('aria-label="Close"', page)
        self.assertIn('id="cam-pick"', page)           # choose one you already have
        self.assertIn('id="cam-fallback" hidden role="alert"', page)
        self.assertIn("Up to 3 minutes", page)

    def test_say_something_has_words_tags_consent_and_a_date(self):
        page = self.client.get(reverse("artifacts:send")).content.decode()
        say = page.split('id="say"')[1]
        for needle in ('name="words"', 'name="tags"', 'name="consent"', 'name="happened_on"', 'role="switch"'):
            self.assertIn(needle, say)
        self.assertNotIn("transcri", page.lower())     # nothing here transcribes yet, so nothing says it does

    def test_tags_and_consent_travel_with_the_upload(self):
        f = SimpleUploadedFile("c.png", png_bytes("teal"), content_type="image/png")
        r = self.client.post(reverse("artifacts:send"), {"files": [f], "words": "Rosa", "tags": "mentoring, Food Business",
                                                         "consent": "on"}, **self.ajax)
        self.assertEqual(r.status_code, 200)
        a = Artifact.objects.get(org=self.org)
        self.assertEqual(sorted(t.name for t in a.tags.all()), ["food business", "mentoring"])
        self.assertTrue(a.consent)
        self.assertEqual(r.json()["items"][0]["title"], "Rosa")

    def test_without_the_switch_nobody_is_recorded_as_agreeing(self):
        f = SimpleUploadedFile("d.png", png_bytes("gold"), content_type="image/png")
        self.client.post(reverse("artifacts:send"), {"files": [f]}, **self.ajax)
        self.assertFalse(Artifact.objects.get(org=self.org).consent)

    def test_what_you_sent_shows_where_each_piece_stands(self):
        mine, _ = add_file(self.org, data=png_bytes("#101010"), filename="m1.png", source=self.upload, words="In a story one", added_by=self.user)
        signed, _ = add_file(self.org, data=png_bytes("#202020"), filename="m2.png", source=self.upload, words="Signed one", added_by=self.user)
        waiting, _ = add_file(self.org, data=png_bytes("#303030"), filename="m3.png", source=self.upload, words="Waiting one", added_by=self.user)
        add_file(self.org, data=png_bytes("#404040"), filename="m4.png", source=self.upload, words="Somebody else's")
        Story.objects.create(org=self.org, title="S", slug="s1")
        StoryBlock.objects.create(story=Story.objects.get(slug="s1"), artifact=mine, text="")
        Artifact.objects.filter(pk=signed.pk).update(claim_id=7, status="kept")
        Artifact.objects.filter(pk=mine.pk).update(status="kept")
        page = self.client.get(reverse("artifacts:send")).content.decode()
        self.assertIn("You sent this week", page)
        self.assertRegex(page, r'state-chip ">In a story<')
        self.assertRegex(page, r'state-chip signed">Signed<')
        self.assertRegex(page, r'state-chip inbox">In inbox<')
        self.assertNotIn("Somebody else", page)

    def test_when_nothing_is_from_this_week_it_says_recently(self):
        old, _ = add_file(self.org, data=png_bytes("#505050"), filename="o.png", source=self.upload, words="Old one", added_by=self.user)
        from datetime import timedelta
        from django.utils import timezone
        Artifact.objects.filter(pk=old.pk).update(received_at=timezone.now() - timedelta(days=30))
        page = self.client.get(reverse("artifacts:send")).content.decode()
        self.assertIn("You sent recently", page)
        self.assertIn("Old one", page)

    def test_a_phone_opens_on_capture_and_a_laptop_on_the_inbox(self):
        add_file(self.org, data=png_bytes("#606060"), filename="w.png", source=self.upload)
        phone = self.client.get(reverse("artifacts:home"), HTTP_USER_AGENT="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Mobile/15E148")
        self.assertRedirects(phone, reverse("artifacts:send"), fetch_redirect_response=False)
        laptop = self.client.get(reverse("artifacts:home"), HTTP_USER_AGENT="Mozilla/5.0 (X11; Linux x86_64) Chrome/130")
        self.assertRedirects(laptop, reverse("artifacts:library") + "?show=inbox", fetch_redirect_response=False)


class SampleOrgTests(Base):
    def test_the_sample_has_numbers_with_what_they_count_and_where_they_came_from(self):
        from django.core.management import call_command
        call_command("sample_org", self.org.slug, stdout=io.StringIO())
        n = Artifact.objects.filter(org=self.org, kind=ArtifactKind.DATA).get(title="40")
        self.assertEqual((n.about, n.source_ref, n.source_url), ("founders in the spring cohort", "Program roster, exported 1 Oct", ""))
        call_command("sample_org", self.org.slug, stdout=io.StringIO())      # safe to run again
        self.assertEqual(Artifact.objects.filter(org=self.org, kind=ArtifactKind.DATA).count(), 2)


class NumberTests(Base):
    """A number is kept with what it counts and where it came from, or it is not kept."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.post = lambda **d: self.client.post(reverse("artifacts:add_number"), d)

    def test_a_number_keeps_what_it_counts_and_where_it_came_from(self):
        r = self.post(value="40", what="Founders in the spring cohort", where="Program roster, exported 1 Oct")
        self.assertRedirects(r, reverse("artifacts:send"), fetch_redirect_response=False)
        a = Artifact.objects.get(org=self.org)
        self.assertEqual((a.kind, a.title, a.about, a.source_ref, a.added_by),
                         (ArtifactKind.DATA, "40", "Founders in the spring cohort", "Program roster, exported 1 Oct", self.user))

    def test_a_number_without_its_source_is_refused(self):
        self.post(value="40", what="Founders", where="")
        self.assertEqual(Artifact.objects.filter(org=self.org).count(), 0)

    def test_the_same_figure_twice_is_two_numbers(self):
        for _ in range(2):
            self.post(value="40", what="Founders", where="Roster")
        self.assertEqual(Artifact.objects.filter(org=self.org, kind=ArtifactKind.DATA).count(), 2)


class LibraryTests(Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        add_file(self.org, data=png_bytes("red"), filename="a.png", content_type="image/png", source=self.upload, words="Bakery counter")
        add_file(self.org, data=b"note", filename="n.txt", content_type="text/plain", source=self.upload, words="Lease signed")
        self.page = lambda q="": self.client.get(reverse("artifacts:library") + q).content.decode()

    def test_segments_filter_by_type_and_mark_the_current_one(self):
        page = self.page()
        self.assertRegex(page, r'<a href="[^"]*"[^>]*aria-current="true">All</a>')
        self.assertIn(">Photos</a>", page)
        photos = self.page("?kind=photo")
        self.assertRegex(photos, r'aria-current="true">Photos</a>')
        self.assertIn("Bakery counter", photos)
        self.assertNotIn("Lease signed", photos)

    def test_filters_live_in_a_sheet_and_show_as_removable_pills(self):
        page = self.page()
        self.assertIn('<dialog id="filters" class="sheet"', page)
        self.assertIn('data-open="filters"', page)
        self.assertNotIn('class="pills"', page)
        tag = Tag.objects.create(org=self.org, name="opening", group=TagGroup.LABEL)
        Artifact.objects.get(org=self.org, kind=ArtifactKind.PHOTO).tags.add(tag)
        filtered = self.page("?tag=opening")
        self.assertIn('class="pills"', filtered)
        self.assertIn("Remove this filter", filtered)
        self.assertIn("Clear all", filtered)

    def test_search_keeps_the_other_filters(self):
        page = self.page("?kind=photo&sort=oldest")
        search = page.split('role="search"')[1].split("</form>")[0]
        self.assertIn('name="kind" value="photo"', search)
        self.assertIn('name="sort" value="oldest"', search)

    def test_inbox_is_what_nobody_has_looked_at_yet(self):
        Artifact.objects.filter(words="Lease signed").update(status="kept")
        inbox = self.page("?show=inbox")
        self.assertIn("Bakery counter", inbox)
        self.assertNotIn("Lease signed", inbox)
        nav = inbox.split('<nav class="topnav"')[1].split("</nav>")[0]
        self.assertRegex(nav, r'show=inbox"[^>]*aria-current="page"')
        self.assertIn("Lease signed", self.page())

    def test_signed_and_missing_consent_are_one_tap_each(self):
        Artifact.objects.filter(words="Lease signed").update(claim_id=9, consent=True)
        signed = self.page("?signed=yes")
        self.assertIn("Lease signed", signed)
        self.assertNotIn("Bakery counter", signed)
        missing = self.page("?consent=missing")
        self.assertIn("Bakery counter", missing)
        self.assertNotIn("Lease signed", missing)
        rail = self.page().split('aria-label="Filters"')[1].split("</aside>")[0]
        self.assertRegex(rail, r'role="checkbox" aria-checked="false"><span class="box"></span>Signed')
        self.assertRegex(self.page("?signed=yes").split('aria-label="Filters"')[1], r'aria-checked="true"><span class="box">.*?Signed')

    def test_the_page_offers_the_ways_to_add_above_the_grid(self):
        page = self.page()
        strip = page.split('aria-labelledby="add-title"')[1].split("</section>")[0]
        for needle in ("Upload files", "Add a link", "Add a number", "Drive folder", "Email it in"):
            self.assertIn(needle, strip)

    def test_a_piece_opens_beside_the_grid_as_a_fragment(self):
        a = Artifact.objects.get(words="Lease signed")
        frag = self.client.get(reverse("artifacts:artifact", args=[a.pk]) + "?panel=1").content.decode()
        self.assertNotIn("<html", frag)
        self.assertIn("Lease signed", frag)
        self.assertIn("Not signed yet", frag)
        self.assertIn("data-panel-close", frag)
        full = self.client.get(reverse("artifacts:artifact", args=[a.pk])).content.decode()
        self.assertIn("<html", full)

    def test_every_card_can_be_picked_and_says_if_it_is_signed(self):
        page = self.page()
        self.assertEqual(page.count('name="ids"'), 2)
        self.assertEqual(page.count("Not signed</span>"), 2)

    def test_my_uploads_marks_its_own_tab(self):
        page = self.page("?person=lead%40example.org")
        tabs = page.split('<nav class="tabs"')[1].split("</nav>")[0]
        self.assertRegex(tabs, r'aria-current="page"[^>]*>.*?<span>My uploads</span>')

    def test_nothing_here_yet_points_at_adding(self):
        Artifact.objects.filter(org=self.org).delete()
        page = self.page()
        self.assertIn("Nothing here yet", page)
        self.assertIn(reverse("artifacts:send"), page)


class PublicStoryTests(Base):
    def setUp(self):
        super().setUp()
        a = add_file(self.org, data=png_bytes("red"), filename="a.png", content_type="image/png", source=self.upload,
                     words="Opening morning", sent_by="private.person@example.org", added_by=self.user)[0]
        a.consent = True
        a.save()
        add_file(self.org, data=b"x", filename="n.txt", content_type="text/plain", source=self.upload, words="A note",
                 sent_by="private.person@example.org")
        self.story = Story.objects.create(org=self.org, title="First weeks", slug="first-weeks", status="published")
        for i, art in enumerate(Artifact.objects.filter(org=self.org).order_by("pk")):
            StoryBlock.objects.create(story=self.story, order=i, artifact=art)
        self.page = self.client.get(reverse("artifacts:story", args=["first-weeks"])).content.decode()

    def test_every_piece_says_how_it_arrived_and_who_agreed(self):
        self.assertIn("Sent from a phone or computer", self.page)
        self.assertIn("Everyone shown agreed", self.page)

    def test_how_it_was_made_lists_the_pieces_and_is_honest_about_signatures(self):
        self.assertIn("How this story was made", self.page)
        self.assertIn("Not signed yet", self.page)

    def test_public_page_never_shows_who_sent_it(self):
        self.assertNotIn("private.person@example.org", self.page)

    def test_story_page_has_one_main(self):
        self.assertEqual(self.page.count("<main"), 1)

    def test_no_side_stripe_on_quotes(self):
        self.assertNotRegex(self.page, r"border-left:\s*[3-9]px")

    def test_a_photo_can_be_checked_without_showing_who_sent_it(self):
        self.assertIn("Check this photo", self.page)
        card = self.page.split('<dl class="proof-card">')[1].split("</dl>")[0]
        for needle in ("Taken", "Came in", "Signed", "Consent", "Everyone shown agreed"):
            self.assertIn(needle, card)

    def test_the_footer_counts_signatures_honestly_and_offers_share_and_print(self):
        self.assertIn("0 of 2 pieces are signed.", self.page)
        self.assertIn('id="share"', self.page)
        self.assertIn("Download as PDF", self.page)
        Artifact.objects.filter(org=self.org).update(claim_id=5)
        page = self.client.get(reverse("artifacts:story", args=["first-weeks"])).content.decode()
        self.assertIn("Every piece in this story links to a signed record.", page)

    def test_a_number_is_a_card_with_its_source_and_whether_it_is_signed(self):
        n, _ = add_reference(self.org, kind=ArtifactKind.DATA, url="number:t1", title="40", source=self.upload,
                             source_ref="Program roster, exported 1 Oct")
        n.about = "founders in the spring cohort"
        n.save()
        StoryBlock.objects.create(story=self.story, order=9, artifact=n)
        page = self.client.get(reverse("artifacts:story", args=["first-weeks"])).content.decode()
        card = page.split('class="stat-card"')[1].split("</div>")[0]
        for needle in ("40", "founders in the spring cohort", "Source: Program roster, exported 1 Oct", "Not signed yet"):
            self.assertIn(needle, card)

    def test_the_masthead_names_the_collection_and_the_month(self):
        c = Collection.objects.create(org=self.org, name="Spring cohort")
        self.story.collection = c
        self.story.save()
        page = self.client.get(reverse("artifacts:story", args=["first-weeks"])).content.decode()
        self.assertRegex(page, r'class="eyebrow">Spring cohort &middot; [A-Z][a-z]+ 20\d\d<')

    def test_a_voice_note_has_a_player_that_works_without_script(self):
        v, _ = add_file(self.org, data=b"RIFFxxxxWAVE", filename="v.wav", content_type="audio/wav", source=self.upload, words="I almost gave up")
        StoryBlock.objects.create(story=self.story, order=8, artifact=v)
        page = self.client.get(reverse("artifacts:story", args=["first-weeks"])).content.decode()
        self.assertIn("data-hear", page)
        self.assertRegex(page, r"<audio id=\"hear-\d+\" src=\"[^\"]+\" controls")


class EditorTests(Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        a = add_file(self.org, data=png_bytes("red"), filename="a.png", content_type="image/png", source=self.upload, words="Counter")[0]
        self.story = Story.objects.create(org=self.org, title="Our year", slug="our-year")
        StoryBlock.objects.create(story=self.story, order=0, artifact=a)
        StoryBlock.objects.create(story=self.story, order=1, text="The end.")
        self.url = reverse("artifacts:story_edit", args=["our-year"])
        self.page = lambda: self.client.get(self.url).content.decode()

    def test_every_piece_can_move_without_dragging(self):
        listed = self.page().split('<ol class="blocks"')[1].split("</ol>")[0]
        self.assertEqual(listed.count('class="move up"'), 2)
        self.assertEqual(listed.count('class="move down"'), 2)
        self.assertEqual(listed.count('aria-label="Take out of story"'), 2)

    def test_state_and_heading(self):
        page = self.page()
        self.assertIn('<h1 class="sr">Edit story: Our year</h1>', page)
        self.assertIn('class="status">Draft</span>', page)
        self.assertIn('name="action" value="publish"', page)
        self.story.status = "published"
        self.story.save()
        live = self.page()
        self.assertIn("Published</span>", live)
        self.assertIn("confirm(", live)  # unpublishing asks first

    def test_a_paragraph_is_labelled_as_one(self):
        self.assertIn("A paragraph", self.page())

    def test_the_pieces_sit_beside_the_story_and_the_story_reads_as_a_document(self):
        page = self.page()
        side = page.split('aria-label="Pieces"')[1].split("</aside>")[0]
        self.assertIn('id="pool"', side)
        self.assertIn("Everything you have", side)
        canvas = page.split('class="canvas"')[1]
        for needle in ("What this story shows", 'class="title-input"', "Drop a piece here, or start typing", 'class="preview"'):
            self.assertIn(needle, canvas)

    def test_the_story_helper_is_only_offered_when_it_is_switched_on(self):
        self.assertNotIn("Story helper", self.page())
        with mock.patch("artifacts.views.ai.enabled", return_value=True):
            self.assertIn("Story helper", self.page())

    def test_a_drafted_paragraph_waits_for_a_person_to_use_edit_or_discard_it(self):
        StoryBlock.objects.filter(story=self.story, text="The end.").update(by_ai=True)
        page = self.page()
        self.assertIn("Suggested by the story helper", page)
        for needle in ("use-ai", "edit-ai", "discard-ai"):
            self.assertIn(needle, page)
        StoryBlock.objects.filter(story=self.story).update(by_ai=False)
        self.assertNotIn("use-ai", self.page().split('<ol class="blocks"')[1].split("</ol>")[0])

    def test_publish_and_preview_stay_in_reach_at_every_width(self):
        bar = self.page().split('class="story-bar"')[1].split('<div class="builder">')[0]
        self.assertIn('name="action" value="publish"', bar)
        self.assertIn("Preview", bar)


class WelcomeTests(Base):
    def setUp(self):
        super().setUp()
        self.page = lambda: self.client.get(reverse("artifacts:home")).content.decode()

    def test_front_door_says_what_it_is_and_shows_the_real_screen(self):
        page = self.page()
        self.assertIn("Real moments.", page)
        self.assertIn("Sign in to start", page)
        self.assertRegex(page, r'<img src="[^"]*welcome-capture[^"]*\.png"[^>]*alt="[^"]{20,}"')
        for step in ("Capture", "Organize", "Share"):
            self.assertIn(f"<h3>{step}</h3>", page)

    def test_demo_link_only_when_a_demo_exists(self):
        self.assertNotIn("Try the demo", self.page())
        with override_settings(ACT_DEMO_ORG=self.org.slug):
            self.assertIn("Try the demo", self.page())


class LargeTextTests(TestCase):
    """Spacing and tap sizes are fixed, text is not, so a bigger text setting reflows instead of overflowing."""

    def css(self):
        import pathlib
        return (pathlib.Path(__file__).parent / "static/artifacts/kit.css").read_text()

    def test_gutters_do_not_grow_with_the_text(self):
        import re
        offenders = re.findall(r"(?<![\w-])(?:padding|margin|gap)(?:-[a-z]+)?: [^;}\n]*\d\.?\d*rem", self.css())
        self.assertEqual(offenders, [], "spacing in rem grows with the text setting; use the --s tokens or px")

    def test_tap_size_is_fixed(self):
        self.assertIn("--touch: 44px;", self.css())

    def test_motion_transparency_and_contrast_have_a_way_out(self):
        css = self.css()
        for query in ("prefers-reduced-motion", "prefers-reduced-transparency", "prefers-contrast"):
            self.assertIn(query, css)
