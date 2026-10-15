"""Pull from every Drive folder and LinkedClaims source now (or one org's)."""
from django.core.management.base import BaseCommand

from artifacts.models import Source, SourceKind
from artifacts.spouts import check


class Command(BaseCommand):
    help = "Copy new files from Drive folders and new LinkedClaims"

    def add_arguments(self, parser):
        parser.add_argument("--org", help="org slug; default all")

    def handle(self, *args, **opts):
        qs = Source.objects.filter(kind__in=[SourceKind.DRIVE, SourceKind.LINKEDCLAIMS]).select_related("org")
        if opts.get("org"):
            qs = qs.filter(org__slug=opts["org"])
        for s in qs:
            self.stdout.write(f"{s}: {check(s)}")
