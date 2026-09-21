"""Push cleaned canonical copy onto the LIVE JREAS rows.

`seed_jreas` only fills fields on first create (get_or_create), so a rename or copy
cleanup never reaches rows that already exist in a deployment. This command does, using
the same constants seed_jreas holds, so the two never drift.

Careful by design: DRY-RUN by default. It prints a before -> after for every field it
would change and writes nothing. Re-run with --write to apply. Review the diff first,
especially if the copy was hand-edited in /admin, because --write overwrites to the
canonical text.

    python manage.py recanonicalize_jreas            # show what would change
    python manage.py recanonicalize_jreas --write     # apply it
"""
from django.core.management.base import BaseCommand

from campaigns.models import CTA, Campaign, Org
from campaigns.management.commands.seed_jreas import (
    ABOUT, CTAS, GOVERNANCE, NAME, SLUG, STORY, SUMMARY, TAGLINE, TITLE,
)


class Command(BaseCommand):
    help = "Rewrite live JREAS org/campaign/CTA copy to the cleaned canonical text (dry-run unless --write)."

    def add_arguments(self, parser):
        parser.add_argument("--write", action="store_true", help="Apply the changes (default: dry-run).")

    def handle(self, *args, **opts):
        write = opts["write"]
        changes = 0

        org = Org.objects.filter(slug=SLUG).first()
        campaign = Campaign.objects.filter(slug=SLUG).first()
        if not org and not campaign:
            self.stdout.write(self.style.WARNING(f"No org/campaign with slug {SLUG!r}. Run seed_jreas first."))
            return

        if org:
            changes += self._apply(org, {"name": NAME, "tagline": TAGLINE,
                                         "about": ABOUT, "governance_text": GOVERNANCE}, write)
        if campaign:
            changes += self._apply(campaign, {"title": TITLE, "summary": SUMMARY, "story": STORY}, write)
            by_kind = {kind: (title, desc, label) for kind, title, desc, label in CTAS}
            for cta in campaign.ctas.all():
                if cta.kind in by_kind:
                    title, desc, label = by_kind[cta.kind]
                    changes += self._apply(cta, {"title": title, "description": desc,
                                                 "button_label": label}, write)

        if not changes:
            self.stdout.write(self.style.SUCCESS("Already canonical. Nothing to change."))
        elif write:
            self.stdout.write(self.style.SUCCESS(f"Applied {changes} field change(s)."))
        else:
            self.stdout.write(self.style.WARNING(
                f"{changes} field(s) would change. Re-run with --write to apply."))

    def _apply(self, obj, fields, write):
        label = f"{obj._meta.model_name} {obj.pk}"
        changed = []
        for field, new in fields.items():
            old = getattr(obj, field)
            if old == new:
                continue
            changed.append(field)
            self.stdout.write(f"\n[{label}] {field}:")
            self.stdout.write(self.style.NOTICE(f"  - {_short(old)}"))
            self.stdout.write(self.style.SUCCESS(f"  + {_short(new)}"))
            setattr(obj, field, new)
        if changed and write:
            obj.save(update_fields=changed)
        return len(changed)


def _short(text):
    text = (text or "").replace("\n", " ")
    return text if len(text) <= 160 else text[:157] + "..."
