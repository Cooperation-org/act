"""Send confirmation mails whose retry is due (1h, 6h, 24h after each failure).

Run from a systemd timer (deploy/act-retry-mail.timer, every 15 minutes).
"""
from django.core.management.base import BaseCommand

from letters.mail import retry_due


class Command(BaseCommand):
    help = "Resend failed signature-confirmation emails that are due for a retry"

    def handle(self, *args, **options):
        sent, failed = retry_due()
        if sent or failed:
            self.stdout.write(f"retry_confirmations: sent {sent}, failed {failed}")
