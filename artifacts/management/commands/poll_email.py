"""Read the intake mailbox and file each email under the org it was sent to.

Run on a timer (every few minutes). Mailbox: ACT_INTAKE_IMAP_HOST/USER/PASSWORD
(Migadu). Each org's address is set on its Email source (config.address); one
mailbox can carry many addresses (aliases)."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from artifacts.spouts import poll_mailbox


class Command(BaseCommand):
    help = "File unread intake email as Story Kit artifacts"

    def handle(self, *args, **opts):
        if not (settings.ACT_INTAKE_IMAP_USER and settings.ACT_INTAKE_IMAP_PASSWORD):
            raise CommandError("ACT_INTAKE_IMAP_USER / ACT_INTAKE_IMAP_PASSWORD not set")
        filed, skipped = poll_mailbox(settings.ACT_INTAKE_IMAP_HOST, settings.ACT_INTAKE_IMAP_USER,
                                      settings.ACT_INTAKE_IMAP_PASSWORD)
        self.stdout.write(f"filed {filed}, no org for {skipped}")
