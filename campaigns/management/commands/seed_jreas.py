"""Seed the Raise the Voices org + JREAS Hub campaign (draft) with verified facts only."""
from django.core.management.base import BaseCommand

from campaigns.models import CTA, Campaign, Org


class Command(BaseCommand):
    help = "Create RTV org + JREAS campaign (draft; publish via admin after review)"

    def handle(self, *args, **opts):
        org, _ = Org.objects.get_or_create(
            slug="rtv", defaults={"name": "Raise the Voices", "website": "https://raisethevoices.org"})
        c, created = Campaign.objects.get_or_create(
            org=org, slug="jreas",
            defaults=dict(
                title="JREAS Hub — free coworking and study space in Gaza",
                organizer="Sameh Jres",
                location="Gaza",
                summary="A free coworking and study space with stable internet and electricity — "
                        "running since December 2024, 390+ operating days, 3,500+ people served.",
                story="JREAS Hub is a free coworking and study space in Gaza with stable internet "
                      "and electricity — running since December 2024, 390+ operating days, 3,500+ "
                      "people served.\n\n[Full story in JREAS's own words to come — being collected "
                      "by volunteers.]\n\nMonthly costs are connectivity, fuel and equipment "
                      "replacement. Receipts are posted as updates below.",
                source_url="https://www.linkedin.com/company/jreas-lab1/",
                status="draft",
            ))
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
                 "Live session with the organizers — how the hub runs, what changes month to month.", "Get a reminder"),
                ("subscribe", "Get updates", "Occasional email updates from the campaign.", "Subscribe"),
            ]
            for i, (kind, title, desc, label) in enumerate(ctas):
                CTA.objects.create(campaign=c, kind=kind, title=title, description=desc,
                                   button_label=label, sort=i)
        self.stdout.write(f"org={org.slug} campaign={c.slug} created={created}")
