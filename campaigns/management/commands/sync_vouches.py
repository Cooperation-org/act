"""Mirror new vouches from LinkedTrust for every published campaign.

The vouched-return view pulls right after someone vouches; this is the backstop for
anything missed (a closed tab, a flaky return redirect). Safe to run on a cron/timer.

    python manage.py sync_vouches            # all published campaigns
    python manage.py sync_vouches --slug jreas-coop
"""
from django.core.management.base import BaseCommand

from campaigns.models import Campaign
from campaigns.vouch_sync import pull_vouches


class Command(BaseCommand):
    help = "Pull new LinkedTrust vouches into local rows for published campaigns."

    def add_arguments(self, parser):
        parser.add_argument("--slug", default="", help="Limit to one campaign slug.")

    def handle(self, *args, **opts):
        qs = Campaign.objects.filter(status="published")
        if opts["slug"]:
            qs = qs.filter(slug=opts["slug"])
        total = 0
        for c in qs:
            n = len(pull_vouches(c))
            total += n
            if n:
                self.stdout.write(f"{c.slug}: {n} new")
        self.stdout.write(self.style.SUCCESS(f"Done. {total} new vouch(es)."))
