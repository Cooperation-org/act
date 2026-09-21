"""Org-scoped admin: volunteers see only their orgs' rows; approvers publish.

Volunteers get is_staff and one VolunteerProfile per org they work with.
Superusers see everything. Publishing is per org: an approver for org A cannot
publish org B's rows even when they can see them.
"""
from django.conf import settings
from django.contrib import admin, messages
from django.utils.html import format_html

from .linkedtrust import sign_testimonial
from .models import CTA, Campaign, Org, Response, ShareLink, Testimonial, Update, VolunteerProfile
from .vouch_sync import pull_vouches


def user_orgs(user):
    """Orgs this user volunteers with (superusers: all)."""
    if user.is_superuser:
        return Org.objects.all()
    return Org.objects.filter(volunteers__user=user)


def approver_orgs(user):
    if user.is_superuser:
        return Org.objects.all()
    return Org.objects.filter(volunteers__user=user, volunteers__is_approver=True)


def _first_org(request):
    return user_orgs(request.user).order_by("pk").first()


class OrgScopedAdmin(admin.ModelAdmin):
    org_path = "org"  # override per model: how to reach the Org from this model
    feature = None  # app slug in settings.ENABLED_APPS; None = always shown

    def _enabled(self):
        return self.feature is None or self.feature in settings.ENABLED_APPS

    def has_module_permission(self, request):
        return self._enabled() and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return self._enabled() and super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        return self._enabled() and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return self._enabled() and super().has_change_permission(request, obj)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(**{f"{self.org_path}__in": user_orgs(request.user)})

    def has_delete_permission(self, request, obj=None):
        return self._enabled() and request.user.is_superuser

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        # Org pickers only offer the editor's own orgs.
        if db_field.remote_field.model is Org and not request.user.is_superuser:
            kwargs["queryset"] = user_orgs(request.user)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


def publishable(modeladmin, request, queryset):
    """The rows of `queryset` the user may publish: those in orgs they approve for."""
    return queryset.filter(**{f"{modeladmin.org_path}__in": approver_orgs(request.user)})


@admin.action(description="Publish selected (approvers only)")
def publish(modeladmin, request, queryset):
    allowed = publishable(modeladmin, request, queryset)
    skipped = queryset.count() - allowed.count()
    n = allowed.update(status="published")
    if n:
        messages.success(request, f"Published {n}.")
    if skipped:
        messages.warning(request, f"{skipped} not published: you are not an approver for that org.")


class SuperuserOnlyAdmin(admin.ModelAdmin):
    """Visible and editable by superusers only. The per-object hooks take an
    optional obj argument, which Django passes on change and list views."""

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(Org)
class OrgAdmin(SuperuserOnlyAdmin):
    list_display = ["slug", "name", "givebutter_account_id"]
    prepopulated_fields = {"slug": ["name"]}
    fieldsets = [
        (None, {"fields": ["name", "slug", "website", "tagline"]}),
        ("Own words (Markdown)", {"fields": ["about", "governance_text", "governance_url"]}),
        ("Money: charity of record's Givebutter account", {"fields": ["givebutter_account_id"]}),
    ]


@admin.register(VolunteerProfile)
class VolunteerProfileAdmin(SuperuserOnlyAdmin):
    list_display = ["user", "org", "is_approver"]
    list_filter = ["org", "is_approver"]


class CTAInline(admin.TabularInline):
    model = CTA
    extra = 0


@admin.register(Campaign)
class CampaignAdmin(OrgScopedAdmin):
    feature = "campaigns"
    org_path = "org"
    list_display = ["slug", "title", "org", "status", "moderate_vouches", "preview"]
    list_filter = ["status"]
    prepopulated_fields = {"slug": ["title"]}
    inlines = [CTAInline]
    actions = [publish, "pull_vouches_now"]
    readonly_fields = ["created", "preview"]

    @admin.action(description="Pull new vouches from LinkedTrust now")
    def pull_vouches_now(self, request, queryset):
        allowed = queryset.filter(org__in=approver_orgs(request.user))
        total = 0
        for c in allowed:
            total += len(pull_vouches(c))
        messages.success(request, f"Mirrored {total} new vouch{'es' if total != 1 else ''}.")
        if queryset.count() - allowed.count():
            messages.warning(request, "Some campaigns skipped: you are not an approver for that org.")

    @admin.display(description="Review link")
    def preview(self, obj):
        if not obj.pk:
            return ""
        url = f"{settings.PUBLIC_URL}{obj.preview_path}"
        return format_html('<a href="{}" target="_blank">{}</a>', url, "open draft" if obj.status != "published" else "open")

    def get_changeform_initial_data(self, request):
        org = _first_org(request)
        return {"org": org.pk} if org else {}


@admin.action(description="Sign as LinkedClaim and publish (approvers only)")
def sign_and_publish(modeladmin, request, queryset):
    allowed = publishable(modeladmin, request, queryset)
    signed = failed = 0
    for t in allowed:
        if not t.claim_id:
            ok, msg = sign_testimonial(t)
            if ok:
                signed += 1
            else:
                failed += 1
                messages.warning(request, f"#{t.pk} not signed: {msg}")
    n = allowed.update(status="published")
    messages.success(request, f"Published {n}; signed {signed} new claim{'s' if signed != 1 else ''}.")
    if failed:
        messages.warning(request, f"{failed} published unsigned. Fix the cause and run “Sign as LinkedClaim” again.")
    if queryset.count() - allowed.count():
        messages.warning(request, "Some rows skipped: you are not an approver for that org.")


@admin.action(description="Sign as LinkedClaim (approvers only)")
def sign_only(modeladmin, request, queryset):
    for t in publishable(modeladmin, request, queryset).filter(claim_id__isnull=True):
        ok, msg = sign_testimonial(t)
        (messages.success if ok else messages.warning)(request, f"#{t.pk}: {msg}")


@admin.action(description="Hide from the page (approvers only)")
def hide_from_page(modeladmin, request, queryset):
    """Pull a vouch off the public page without deleting it or the underlying claim."""
    n = publishable(modeladmin, request, queryset).update(status="pending")
    messages.success(request, f"Hid {n} from the page. They stay in LinkedTrust; publish to show again.")


@admin.action(description="Hide every vouch from this source (approvers only)")
def hide_source(modeladmin, request, queryset):
    """Harassment control: hide all vouches sharing a source (the voucher) with any selected row."""
    allowed = publishable(modeladmin, request, queryset)
    sources = {s for s in allowed.values_list("source_uri", flat=True) if s}
    if not sources:
        messages.warning(request, "No source on the selected rows (only vouches carry one).")
        return
    hidden = Testimonial.objects.filter(
        campaign__org__in=approver_orgs(request.user), source_uri__in=sources
    ).update(status="pending")
    messages.success(request, f"Hid {hidden} vouch{'es' if hidden != 1 else ''} from {len(sources)} source(s).")


@admin.register(Testimonial)
class TestimonialAdmin(OrgScopedAdmin):
    feature = "campaigns"
    org_path = "campaign__org"
    list_display = ["campaign", "relationship", "show_identity", "has_video", "claim_id", "source_uri", "status", "created"]
    list_filter = ["status"]
    actions = [sign_and_publish, sign_only, hide_from_page, hide_source]
    readonly_fields = ["claim_id", "linkedclaim_uri", "source_uri", "signed_at", "sign_error", "created"]

    @admin.display(boolean=True, description="Video")
    def has_video(self, obj):
        return bool(obj.video_src)


@admin.register(Update)
class UpdateAdmin(OrgScopedAdmin):
    feature = "campaigns"
    org_path = "campaign__org"
    list_display = ["campaign", "date", "status"]
    list_filter = ["status", "campaign"]
    actions = [publish]


@admin.register(Response)
class ResponseAdmin(OrgScopedAdmin):
    feature = "campaigns"
    org_path = "cta__campaign__org"
    list_display = ["name", "email", "cta", "handled", "created"]
    list_filter = ["handled", "cta__kind"]
    list_editable = ["handled"]
    readonly_fields = ["details", "created"]


@admin.register(ShareLink)
class ShareLinkAdmin(OrgScopedAdmin):
    feature = "campaigns"
    org_path = "campaign__org"
    list_display = ["code", "campaign", "volunteer", "clicks"]
    readonly_fields = ["clicks", "code"]
