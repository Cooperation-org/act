"""Give an org its email address (an alias on the intake mailbox)."""
from django.core.management.base import BaseCommand, CommandError

from artifacts.models import Source, SourceKind
from campaigns.models import Org


class Command(BaseCommand):
    help = "Set the address an org emails its material to"

    def add_arguments(self, parser):
        parser.add_argument("org", help="org slug")
        parser.add_argument("address")

    def handle(self, *args, org, address, **opts):
        o = Org.objects.filter(slug=org).first()
        if not o:
            raise CommandError(f"no org {org}")
        s, _ = Source.objects.get_or_create(org=o, kind=SourceKind.EMAIL, defaults={"label": "Email"})
        s.config = {**s.config, "address": address.strip().lower()}
        s.save(update_fields=["config"])
        self.stdout.write(f"{o.name}: {s.config['address']}")
