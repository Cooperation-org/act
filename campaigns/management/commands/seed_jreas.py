"""Seed the Jreas Coop org + JREAS Hub campaign (draft) with verified facts only.

Safe to re-run: get_or_create, never overwrites edits. The campaign is created
under org `jreas-coop`; a campaign that already exists under another org is moved.
"""
from django.core.management.base import BaseCommand

from campaigns.models import CTA, Campaign, Org

SLUG = "jreas-coop"  # org and campaign slug: /c/jreas-coop/, ACT_SITE_ORG=jreas-coop


class Command(BaseCommand):
    help = "Create Jreas Coop org + JREAS campaign (draft; publish via admin after review)"

    def handle(self, *args, **opts):
        Org.objects.filter(slug="jreas").update(slug=SLUG)  # earlier seeds used "jreas"
        Campaign.objects.filter(slug="jreas").update(slug=SLUG)
        org, _ = Org.objects.get_or_create(
            slug=SLUG, defaults={"name": "Jreas Coop", "website": "https://www.linkedin.com/company/jreas-lab1/"})
        c = Campaign.objects.filter(slug=SLUG).first()
        created = c is None
        if c is None:
            c = Campaign.objects.create(
                org=org, slug=SLUG,
                title="JREAS Hub: free coworking and study space in Gaza",
                organizer="Sameh Jres",
                location="Gaza",
                summary="A free coworking and study space with stable internet and electricity, "
                        "running since December 2024, 390+ operating days, 3,500+ people served.",
                story="JREAS Hub is a free coworking and study space in Gaza with stable internet "
                      "and electricity, running since December 2024, 390+ operating days, 3,500+ "
                      "people served.\n\n[Full story in JREAS's own words to come, being collected "
                      "by volunteers.]\n\nMonthly costs are connectivity, fuel and equipment "
                      "replacement. Receipts are posted as updates below.",
                source_url="https://www.linkedin.com/company/jreas-lab1/",
                status="draft",
            )
        elif c.org_id != org.pk:
            c.org = org
            c.save(update_fields=["org"])
        if created:
            ctas = [
                ("give", "Give", "Every route shows its cost before you pay.", "Give"),
                ("mentor", "Mentor & practice English",
                 "A weekly half-hour call from the hub. Conversation practice, code review, or company.", "Volunteer"),
                ("hire", "Hire someone",
                 "Developers and translators working from the hub take remote contracts.", "I'm hiring"),
                ("event", "Host an event",
                 "Pair with the team for a fundraiser night, a talk, or a livestream from the hub.", "Pair up"),
                ("ama", "Ask Me Anything",
                 "Live session with the organizers: how the hub runs, what changes month to month.", "Get a reminder"),
                ("podcast", "Podcast tie-in",
                 "Run a show? Book a live segment from the hub; your listeners give directly during the episode.",
                 "Book a segment"),
                ("subscribe", "Get updates", "Occasional email updates from the campaign.", "Subscribe"),
            ]
            for i, (kind, title, desc, label) in enumerate(ctas):
                CTA.objects.create(campaign=c, kind=kind, title=title, description=desc,
                                   button_label=label, sort=i)
        elif not c.ctas.filter(kind="podcast").exists():
            CTA.objects.create(campaign=c, kind="podcast", title="Podcast tie-in", button_label="Book a segment",
                               description="Run a show? Book a live segment from the hub; your listeners give "
                                           "directly during the episode.", sort=5)
        self.stdout.write(f"org={org.slug} campaign={c.slug} created={created}")
