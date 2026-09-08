"""Letters admin, scoped to the editor's org (superusers see all).

Signatures: filter by letter, confirmation state, city or country; tick which
appear on the PDF and set their order in the list; resend a confirmation;
tick "approved" to count a signature whose email never confirmed; export the
selected rows as the PDF.
"""
import csv

from django.contrib import admin, messages
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from campaigns.admin import OrgScopedAdmin, publish

from .mail import confirm_url, send_confirmation
from .models import EmailLog, Letter, Signature, SignatureField
from .pdf import letter_pdf


LOCKED_LETTER_FIELDS = ["org", "title", "slug", "kind", "body", "addressee"]
LOCKED_FIELD_FIELDS = ["key", "kind"]  # labels may be reworded; stored shape may not


def _locked(letter):
    """A letter with any signature is what people signed: its text is frozen."""
    return bool(letter and letter.pk and letter.signatures.exists())


class SignatureFieldInline(admin.TabularInline):
    model = SignatureField
    extra = 0
    fields = ["label", "key", "kind", "required", "public", "sort"]

    def get_readonly_fields(self, request, obj=None):
        return LOCKED_FIELD_FIELDS if _locked(obj) else []

    def has_delete_permission(self, request, obj=None):
        return not _locked(obj) and super().has_delete_permission(request, obj)


@admin.register(Letter)
class LetterAdmin(OrgScopedAdmin):
    feature = "letters"
    list_display = ["title", "org", "kind", "status", "signature_count", "open"]
    list_filter = ["status", "kind", "org"]
    prepopulated_fields = {"slug": ["title"]}
    inlines = [SignatureFieldInline]
    actions = [publish]
    readonly_fields = ["created", "published_at", "open", "locked"]
    fieldsets = [
        (None, {"fields": ["org", "title", "slug", "kind", "status", "open", "locked"]}),
        ("Text", {"fields": ["body"]}),
        ("Petition", {"fields": ["addressee", "goal"], "classes": ["collapse"]}),
        ("Page", {"fields": ["theme", "show_signatures", "updates_label", "created", "published_at"]}),
    ]

    @admin.display(description="Signatures")
    def signature_count(self, obj):
        url = reverse("admin:letters_signature_changelist") + f"?letter__id__exact={obj.pk}"
        return format_html('<a href="{}">{}</a>', url, obj.count())

    @admin.display(description="Text")
    def locked(self, obj):
        if _locked(obj):
            return format_html("<b>Locked</b>: {} signature(s) exist, so the title, kind and text can no longer "
                               "be changed. Question labels may still be reworded; questions cannot be removed.",
                               obj.signatures.count())
        return "Editable until the first signature."

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        return fields + LOCKED_LETTER_FIELDS if _locked(obj) else fields

    def get_prepopulated_fields(self, request, obj=None):
        return {} if _locked(obj) else super().get_prepopulated_fields(request, obj)

    @admin.display(description="Page")
    def open(self, obj):
        if not obj.pk:
            return ""
        url = reverse("letters:letter", args=[obj.slug])
        pdf = reverse("letters:pdf", args=[obj.slug])
        return format_html('<a href="{}" target="_blank">{}</a> · <a href="{}" target="_blank">PDF</a>', url, url, pdf)

    def save_model(self, request, obj, form, change):
        if obj.status == "published" and not obj.published_at:
            obj.published_at = timezone.now()
        super().save_model(request, obj, form, change)


class ConfirmationFilter(admin.SimpleListFilter):
    title = "confirmation"
    parameter_name = "confirmation"

    def lookups(self, request, model_admin):
        return [
            ("confirmed", "Email confirmed"),
            ("drawn", "Signed by drawing"),
            ("approved", "Approved by admin"),
            ("waiting", "Sent, waiting"),
            ("retrying", "Email failed, retry scheduled"),
            ("gave_up", "Email failed, gave up"),
            ("failed", "Email failed (any)"),
            ("unsent", "No email sent"),
        ]

    def queryset(self, request, qs):
        v = self.value()
        if v == "confirmed":
            return qs.filter(confirmed_at__isnull=False)
        if v == "drawn":
            return qs.exclude(drawn="")
        if v == "waiting":
            return qs.filter(confirmed_at__isnull=True, drawn="", confirm_sent_at__isnull=False)
        if v == "approved":
            return qs.filter(approved=True)
        if v == "retrying":
            return qs.filter(confirmed_at__isnull=True, confirm_next_retry_at__isnull=False)
        if v == "gave_up":
            return qs.filter(confirmed_at__isnull=True, confirm_next_retry_at__isnull=True).exclude(confirm_last_error="")
        if v == "failed":
            return qs.filter(confirmed_at__isnull=True).exclude(confirm_last_error="")
        if v == "unsent":
            return qs.filter(confirmed_at__isnull=True, drawn="", confirm_sent_at__isnull=True)
        return qs


@admin.action(description="Resend confirmation email")
def resend_confirmation(modeladmin, request, queryset):
    sent = failed = 0
    for s in queryset.exclude(email="").filter(confirmed_at__isnull=True):
        log = send_confirmation(s, confirm_url(s, request))
        if log.error:
            failed += 1
        else:
            sent += 1
    level = messages.WARNING if failed else messages.SUCCESS
    modeladmin.message_user(request, f"Sent {sent}, failed {failed}.", level)


@admin.action(description="PDF of the letter with the selected signatures")
def export_pdf(modeladmin, request, queryset):
    letters = {s.letter_id for s in queryset}
    if len(letters) != 1:
        modeladmin.message_user(request, "Select signatures from one letter.", messages.ERROR)
        return
    letter = queryset.first().letter
    sigs = queryset.valid().order_by("pdf_sort", "created")
    data = letter_pdf(letter, sigs, request.build_absolute_uri("/"))
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{letter.slug}-{sigs.count()}-signatures.pdf"'
    return response


@admin.action(description="CSV of the selected signatures")
def export_csv(modeladmin, request, queryset):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="signatures.csv"'
    keys = sorted({k for s in queryset for k in s.extras})
    w = csv.writer(response)
    w.writerow(["letter", "first_name", "last_name", "email", "city", "country", "keep_updated",
                "confirmation", "signed_at", *keys])
    for s in queryset.select_related("letter").order_by("created"):
        w.writerow([s.letter.slug, s.first_name, s.last_name, s.email, s.city, s.country, s.keep_updated,
                    s.confirmation, s.created.isoformat(timespec="seconds"), *[s.extras.get(k, "") for k in keys]])
    return response


@admin.action(description="Approve: count and show without confirmation")
def approve(modeladmin, request, queryset):
    queryset.update(approved=True)


@admin.action(description="Hide from page and PDF")
def hide(modeladmin, request, queryset):
    queryset.update(hidden=True)


@admin.action(description="Show on page and PDF")
def show(modeladmin, request, queryset):
    queryset.update(hidden=False)


@admin.register(Signature)
class SignatureAdmin(OrgScopedAdmin):
    feature = "letters"
    org_path = "letter__org"
    list_display = ["letter", "full_name", "email", "city", "country", "confirmation_status", "keep_updated",
                    "signed", "approved", "on_pdf", "pdf_sort", "hidden", "created"]
    list_display_links = ["full_name"]
    list_editable = ["approved", "on_pdf", "pdf_sort", "hidden"]
    ordering = ["letter__title", "-created"]  # grouped by letter, newest first within each
    list_filter = ["letter", ConfirmationFilter, "approved", "keep_updated", "on_pdf", "hidden", "country", "city"]
    search_fields = ["first_name", "last_name", "email", "city", "country", "extras"]
    actions = [export_pdf, export_csv, resend_confirmation, approve, hide, show]
    readonly_fields = ["token", "confirmed_at", "confirm_sent_at", "confirm_sends", "confirm_attempts",
                       "confirm_last_attempt_at", "confirm_last_error", "confirm_next_retry_at",
                       "ip", "created", "signed", "mail"]
    fieldsets = [
        (None, {"fields": ["letter", "first_name", "last_name", "email", "city", "country", "extras",
                           "keep_updated", "drawn", "signed"]}),
        ("Shown", {"fields": ["approved", "hidden", "on_pdf", "pdf_sort"]}),
        ("Confirmation", {"fields": ["confirmed_at", "confirm_sent_at", "confirm_sends", "confirm_attempts",
                                     "confirm_last_attempt_at", "confirm_last_error", "confirm_next_retry_at",
                                     "mail"]}),
        ("Record", {"fields": ["ip", "created", "token"], "classes": ["collapse"]}),
    ]
    list_per_page = 100
    date_hierarchy = "created"

    @admin.display(description="Confirmation")
    def confirmation_status(self, obj):
        status = obj.confirmation
        if status == "retrying":
            return format_html("retrying (next {})", timezone.localtime(obj.confirm_next_retry_at).strftime("%b %d %H:%M"))
        if status == "gave up":
            return format_html("gave up after {} tries", obj.confirm_attempts)
        return status

    @admin.display(description="Drawn")
    def signed(self, obj):
        if obj.drawn:
            return format_html('<img src="{}" alt="" style="height:28px;background:#fff">', obj.drawn.url)
        return ""

    @admin.display(description="Mail")
    def mail(self, obj):
        rows = [f"{e.sent_at:%Y-%m-%d %H:%M} {e.subject}: {e.error or 'sent'}" for e in obj.emails.all()[:10]]
        return format_html("<br>".join(["{}"] * len(rows)), *rows) if rows else ""


@admin.register(EmailLog)
class EmailLogAdmin(OrgScopedAdmin):
    feature = "letters"
    org_path = "letter__org"
    list_display = ["sent_at", "to", "subject", "status", "letter"]
    list_filter = ["letter"]
    search_fields = ["to", "subject", "error"]
    readonly_fields = ["letter", "signature", "to", "subject", "sent_at", "error"]

    @admin.display(description="Status")
    def status(self, obj):
        return obj.error or "sent"

    def has_add_permission(self, request):
        return False
