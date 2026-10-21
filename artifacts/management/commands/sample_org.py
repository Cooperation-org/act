"""Fill an org with made-up sample material for walkthroughs and testing:
three sites (labels "part of the org"), five businesses, items from April to
October from staff, mentors and the owners themselves. Every name, number and
quote here is invented. Safe to run again: the same item is never added twice.

    manage.py sample_org umbrella-collective
"""
import io
import subprocess
import tempfile
from datetime import datetime, timezone

from django.core.management.base import BaseCommand, CommandError

from campaigns.models import Org

from ...intake import add_file, add_reference
from ...models import ArtifactKind, Source, SourceKind, Tag, TagGroup

STAFF, MENTOR = "jordan@umbrella.example", "lee.mentor@umbrella.example"

# (site, business, owner email, [(date, kind, title, words, extra labels)])
# kind: photo, voice, video, text, link. Items with labels=None are left unlabeled,
# so Sort with AI has something to do.
SAMPLE = [
    ("main street", "main street bakery", "maria@mainstreetbakery.example", [
        ("2026-05-12", "photo", "Pitch night", "Maria pitching the bakery plan to the spring cohort.", ["pitch"]),
        ("2026-07-02", "text", "Lease signed", "Signed a two-year lease on the corner storefront. Rent $1,850 a month.", ["milestone"]),
        ("2026-10-08", "photo", "Second oven", "Main Street Bakery bought a second oven with the October sales. Two people hired.", None),
    ]),
    ("riverside", "riverside bike repair", "dev@riversidebikes.example", [
        ("2026-04-20", "photo", "Pop-up stand", "First weekend fixing bikes under a tent by the river path.", ["pop-up"]),
        ("2026-06-03", "voice", "Why bikes", "Dev: \"Half my customers ride to work. If the bike breaks, they lose the job.\"", ["testimonial"]),
        ("2026-07-18", "text", "June numbers", "June: 64 repairs, $3,120 in sales, 9 bikes refurbished and sold.", ["revenue"]),
        ("2026-09-06", "photo", "Shop opening", "Riverside Bike Repair moved from the tent into a garage bay on Water Street.", None),
        ("2026-10-11", "video", "Kids' clinic", "Free Saturday clinic at Riverside Bike Repair: 14 kids learned to fix a flat.", None),
    ]),
    ("riverside", "green thread tailoring", "amina@greenthread.example", [
        ("2026-05-02", "photo", "Sewing table", "Amina's home studio, before the cohort.", ["before"]),
        ("2026-06-21", "link", "Online shop", "Amina's new online shop for altered and mended clothes.", ["online"]),
        ("2026-08-14", "text", "First wholesale order", "A local boutique ordered 40 mended jackets for the fall.", ["milestone"]),
        ("2026-10-01", "voice", "Mentor check-in", "Lee on Green Thread Tailoring: \"She priced too low at first. Now she charges for her time.\"", None),
    ]),
    ("hillcrest", "hillcrest family childcare", "rosa@hillcrestcare.example", [
        ("2026-04-28", "photo", "License day", "Rosa holding the state license for six children.", ["milestone"]),
        ("2026-06-10", "photo", "Reading corner", "The reading corner parents helped build.", ["parents"]),
        ("2026-08-30", "text", "Waitlist", "Hillcrest Family Childcare is full at six children; nine families on the waitlist.", None),
        ("2026-10-15", "voice", "A parent", "A parent at Hillcrest Family Childcare: \"I could take the night shift because Rosa opens at six.\"", None),
    ]),
    ("hillcrest", "two rivers coffee cart", "sam@tworivers.example", [
        ("2026-05-25", "photo", "Cart build", "Sam and a mentor building the cart from a used trailer.", ["before"]),
        ("2026-07-04", "video", "Fourth of July", "Line at the cart during the parade.", ["event"]),
        ("2026-08-22", "text", "Health permit", "Passed the county mobile food inspection on the first try.", ["milestone"]),
        ("2026-09-27", "photo", "Farmers market", "Two Rivers Coffee Cart has a weekly spot at the Hillcrest farmers market.", None),
    ]),
]

COLORS = ["#2f6f8f", "#8a5a2b", "#3d7a4a", "#7a3d6b", "#a0522d", "#4b5d8a", "#6b7a3d"]


def _when(day):
    return datetime.strptime(day, "%Y-%m-%d").replace(hour=12, tzinfo=timezone.utc)


def _photo(title, business, n):
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (1200, 900), COLORS[n % len(COLORS)])
    d = ImageDraw.Draw(im)
    big, small = ImageFont.load_default(size=72), ImageFont.load_default(size=36)
    d.text((70, 330), title, font=big, fill="white")
    d.text((70, 440), business.title(), font=small, fill="#f0f0f0")
    d.text((70, 820), "Sample image", font=small, fill="#dddddd")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def _ffmpeg(args, suffix):
    with tempfile.NamedTemporaryFile(suffix=suffix) as out:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args, out.name], check=True)
        return open(out.name, "rb").read()


def _voice(n):
    return _ffmpeg(["-f", "lavfi", "-i", f"sine=frequency={330 + 40 * n}:duration=3"], ".wav")


def _video(n):
    color = COLORS[n % len(COLORS)].lstrip("#")
    return _ffmpeg(["-f", "lavfi", "-i", f"color=c=0x{color}:s=640x360:d=3", "-f", "lavfi",
                    "-i", f"sine=frequency={220 + 30 * n}:duration=3", "-shortest",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"], ".mp4")


class Command(BaseCommand):
    help = "Add made-up sample material (three sites, five businesses) to an org."

    def add_arguments(self, parser):
        parser.add_argument("org_slug")

    def handle(self, org_slug, **opts):
        org = Org.objects.filter(slug=org_slug).first()
        if org is None:
            raise CommandError(f"No org {org_slug}")
        upload = Source.objects.filter(org=org, kind=SourceKind.UPLOAD).first() or \
            Source.objects.create(org=org, kind=SourceKind.UPLOAD, label="Phone and computer")
        links = Source.objects.filter(org=org, kind=SourceKind.LINK).first() or \
            Source.objects.create(org=org, kind=SourceKind.LINK, label="Links")
        made = n = 0
        for site, business, owner, items in SAMPLE:
            for day, kind, title, words, labels in items:
                n += 1
                sender = [owner, STAFF, MENTOR][n % 3] if kind != "voice" else STAFF
                when = _when(day)
                common = dict(words=words, sent_by=sender, happened_at=when)
                if kind == "link":
                    a, created = add_reference(org, kind=ArtifactKind.LINK, source=links, title=title,
                                               url=f"https://{owner.split('@')[1]}/shop", **common)
                else:
                    data, name, ctype = {
                        "photo": lambda: (_photo(title, business, n), f"{title.lower().replace(' ', '-')}.jpg", "image/jpeg"),
                        "voice": lambda: (_voice(n), f"{title.lower().replace(' ', '-')}.wav", "audio/wav"),
                        "video": lambda: (_video(n), f"{title.lower().replace(' ', '-')}.mp4", "video/mp4"),
                        "text": lambda: (words.encode(), f"{title.lower().replace(' ', '-')}.txt", "text/plain"),
                    }[kind]()
                    a, created = add_file(org, data=data, filename=name, content_type=ctype, source=upload,
                                          title=title, **common)
                    if a.happened_at != when:  # a photo's own date wins in intake; sample dates are the story
                        a.happened_at = when
                        a.save(update_fields=["happened_at"])
                made += created
                if labels is not None:
                    a.tags.add(Tag.objects.get_or_create(org=org, name=site, defaults={"group": TagGroup.PART})[0],
                               Tag.objects.get_or_create(org=org, name=business, defaults={"group": TagGroup.PART})[0],
                               *[Tag.objects.get_or_create(org=org, name=x)[0] for x in labels])
        self.stdout.write(f"{made} new sample items in {org.name} ({n} in the sample).")
