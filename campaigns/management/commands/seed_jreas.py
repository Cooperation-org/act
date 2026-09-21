"""Seed the JREAS Coop Center org + campaign (draft) from JREAS's own profile.

Content source: JREAS's official EN profile deck (Sept 2026) and their LinkedIn.
The story, summary and numbers are JREAS's own words. The governance note and the
call-to-action copy are DRAFT wording, to be replaced with JREAS's/golda's own text
in /admin before publishing. Testimonials are loaded as PENDING (never shown until an
approver publishes them, and names stay hidden unless the person opts in).

Safe to re-run: get_or_create / guarded creates, never overwrites admin edits. To push
cleaned canonical copy onto rows that already exist, use `manage.py recanonicalize_jreas`.
Publish via /admin (Campaigns → jreas-coop → Publish) after review.
"""
from django.core.management.base import BaseCommand

from campaigns.models import CTA, Campaign, Org, PublishStatus, Testimonial

SLUG = "jreas-coop"
NAME = "JREAS Coop Center"
TITLE = "JREAS Coop Center: a free place to study and work in Gaza"
TAGLINE = "Free workspaces for students and freelancers in war-torn Gaza."

# JREAS's own words (from their profile deck; OCR artifacts cleaned).
SUMMARY = (
    "A completely free workspace and study center in Gaza with stable internet and continuous "
    "electricity. Launched December 2024; in under 16 months it has served 3,500+ beneficiaries "
    "across 390+ operating days and 3,000+ service hours, all 100% free."
)
STORY = (
    "Over 100,000 students in Gaza could not sit a single online exam. Over 20,000 freelancers "
    "lost their international contracts overnight when the power went out and the network died.\n\n"
    "The JREAS Coop Center was born from that need. In December 2024 a group of determined young "
    "Palestinians opened it in the heart of Gaza: a completely free workspace and study center "
    "offering stable internet, continuous electricity, and a professional environment.\n\n"
    "It began with Wissal Jreas, a master's student who stood in the street holding her phone "
    "toward the sky as planes roared overhead, moving from street to street to find a signal, and "
    "reached her exam only to read: “Exam time has ended.” She asked why there was no single safe "
    "place with electricity, internet, and a roof where a person could learn. The JREAS Coop Center "
    "started with two study seats, one for Wissal and one for her brother Moamen, and a decision to "
    "build it by hand and keep it completely free for everyone.\n\n"
    "From one room in the Al-Samer area it grew: a second branch for young men, a merged space, "
    "then the Sharab Commercial Complex serving up to 240 students and freelancers, until a "
    "September attack destroyed the rented premises and forced displacement to southern Gaza. The "
    "team returned north and reopened. In October 2025 the initiative was formally registered as "
    "JreasLab, its executive arm.\n\n"
    "Behind the numbers are real people: graduates who earned academic distinctions, students who "
    "won scholarships in Ireland, Indonesia and Romania, and freelancers who regained international "
    "contracts. “The internet was cut off, but the idea never was.”\n\n"
    "Every dollar buys more than a seat. It buys a lifelong opportunity for young people who "
    "refused to surrender."
)
ABOUT = (
    "The JREAS Coop Center is a free coworking and study space in Gaza with stable internet, "
    "continuous electricity, a professional environment, and free training in digital skills, for "
    "students and freelancers cut off from the world by war. Running since December 2024; "
    "registered as JreasLab in October 2025."
)
# DRAFT — golda/JREAS to confirm the exact wording of the cooperative commitment.
GOVERNANCE = (
    "The JREAS Coop Center is committed to becoming a cooperative, governed democratically by the "
    "students and freelancers who use it, so the people it serves share in the decisions that shape "
    "it.\n\n"
    "*(Draft wording, pending JREAS's own statement.)*"
)
LINKEDIN = "https://www.linkedin.com/company/jreas-lab1/"

# DRAFT call-to-action copy (agent-written; replace with JREAS's/golda's words in /admin).
CTAS = [
    ("give", "Give", "Every route shows its cost before you pay.", "Give"),
    ("mentor", "Mentor & practice English",
     "A weekly half-hour call from the center: conversation practice, code review, or company.", "Volunteer"),
    ("networking", "Open your network",
     "Freelancers at the center who lost clients need introductions. Connect them to people who "
     "hire.", "Make an intro"),
    ("hire", "Hire from the center",
     "Developers, designers and translators working from the center take remote contracts.", "I'm hiring"),
    ("event", "Host an event",
     "Pair with the team for a fundraiser night, a talk, or a livestream from the center.", "Pair up"),
    ("ama", "Ask Me Anything",
     "A live session with the organizers: how the center runs, what changes month to month.", "Get a reminder"),
    ("podcast", "Podcast tie-in",
     "Run a show? Book a live segment from the center; your listeners give directly during the episode.",
     "Book a segment"),
    ("subscribe", "Get updates", "Occasional email updates from the campaign.", "Subscribe"),
]

# Real testimonials from JREAS's profile deck. Loaded PENDING; names stay hidden until the
# person opts in (show_identity) and an approver publishes. Consent must be on record first.
TESTIMONIALS = [
    ("Lubna Al-Jarrah", "student",
     "My academic journey began amid real challenges. I took exams under unstable conditions and a "
     "weak internet connection. Joining the JREAS Coop Center was a pivotal turning point: an "
     "environment that embraces ambition and gives a sense of focus and belonging. Thanks to this "
     "support I completed a practical course that required a computer I didn't have access to, and "
     "finished my projects successfully. This support turned challenges into achievements."),
    ("Najah Abu Za'nouna", "student",
     "I excelled in high school with a 98.4% average, then the war changed everything: displacement, "
     "loss of home, harsh conditions. I had to complete my field training but found no entity willing "
     "to accept me. In the midst of that darkness, the JREAS Coop Center gave me back my path. I "
     "graduated with a 93.9% average, first in the commercial departments. Success is a decision."),
    ("Yahya Al-Halaw", "freelancer",
     "Joining the JREAS Coop Center was a real golden opportunity. Within one year I obtained 16 "
     "professional certificates in mobile app development through edX, an achievement that would not "
     "have been possible without the supportive environment the initiative provided."),
]


class Command(BaseCommand):
    help = "Create the JREAS Coop Center org + campaign (draft) from JREAS's own profile content."

    def handle(self, *args, **opts):
        Org.objects.filter(slug="jreas").update(slug=SLUG)  # earlier seeds used "jreas"
        Campaign.objects.filter(slug="jreas").update(slug=SLUG)
        org, _ = Org.objects.get_or_create(slug=SLUG, defaults={
            "name": NAME,
            "website": LINKEDIN,
            "tagline": TAGLINE,
            "about": ABOUT,
            "governance_text": GOVERNANCE,
        })
        c = Campaign.objects.filter(slug=SLUG).first()
        created = c is None
        if created:
            c = Campaign.objects.create(
                org=org, slug=SLUG,
                title=TITLE,
                organizer="",  # who to name publicly is golda's call; left blank on purpose
                location="Gaza",
                summary=SUMMARY,
                story=STORY,
                source_url=LINKEDIN,
                status=PublishStatus.DRAFT,
            )
        elif c.org_id != org.pk:
            c.org = org
            c.save(update_fields=["org"])

        for i, (kind, title, desc, label) in enumerate(CTAS):
            CTA.objects.get_or_create(campaign=c, kind=kind, defaults={
                "title": title, "description": desc, "button_label": label, "sort": i})

        for name, rel, quote in TESTIMONIALS:
            Testimonial.objects.get_or_create(campaign=c, display_name=name, defaults={
                "quote": quote, "relationship": rel,
                "show_identity": False, "status": PublishStatus.PENDING})

        self.stdout.write(f"org={org.slug} campaign={c.slug} created={created} "
                          f"ctas={c.ctas.count()} testimonials={c.testimonials.count()} (all draft/pending)")
