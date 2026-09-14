"""Org-scoped admin: volunteers see only their orgs' rows; approvers publish.

Volunteers get is_staff and one VolunteerProfile per org they work with.
Superusers see everything. Publishing is per org: an approver for org A cannot
publish org B's rows even when they can see them.
"""
from django.conf import settings
from django.contrib import admin, messages
from django.utils.html import format_html

from .models import CTA, Campaign, Org, Response, ShareLink, Testimonial, Update, VolunteerProfile


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
        ("Money — charity of record's Givebutter account", {"fields": ["givebutter_account_id"]}),
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
    list_display = ["slug", "title", "org", "status", "preview"]
    list_filter = ["status"]
    prepopulated_fields = {"slug": ["title"]}
    inlines = [CTAInline]
    actions = [publish]
    readonly_fields = ["created", "preview"]

    @admin.display(description="Review link")
    def preview(self, obj):
        if not obj.pk:
            return ""
        url = f"{settings.PUBLIC_URL}{obj.preview_path}"
        return format_html('<a href="{}" target="_blank">{}</a>', url, "open draft" if obj.status != "published" else "open")

    def get_changeform_initial_data(self, request):
        org = _first_org(request)
        return {"org": org.pk} if org else {}


@admin.register(Testimonial)
class TestimonialAdmin(OrgScopedAdmin):
    feature = "campaigns"
    org_path = "campaign__org"
    list_display = ["campaign", "relationship", "show_identity", "status", "created"]
    list_filter = ["status"]
    actions = [publish]


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


@admin.register(ShareLink)
class ShareLinkAdmin(OrgScopedAdmin):
    feature = "campaigns"
    org_path = "campaign__org"
    list_display = ["code", "campaign", "volunteer", "clicks"]
    readonly_fields = ["clicks", "code"]
