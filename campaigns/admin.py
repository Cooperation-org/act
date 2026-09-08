"""Org-scoped admin: volunteers see only their org's rows; approvers publish.

Volunteers get is_staff + group 'volunteers' (created by seed command) and a
VolunteerProfile. Superusers see everything.
"""
from django.conf import settings
from django.contrib import admin

from .models import CTA, Campaign, Org, Response, ShareLink, Testimonial, Update, VolunteerProfile


def _user_org(request):
    profile = getattr(request.user, "volunteer", None)
    return profile.org if profile else None


def _is_approver(request):
    profile = getattr(request.user, "volunteer", None)
    return request.user.is_superuser or (profile and profile.is_approver)


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
        org = _user_org(request)
        return qs.filter(**{self.org_path: org}) if org else qs.none()

    def has_delete_permission(self, request, obj=None):
        return self._enabled() and request.user.is_superuser


@admin.action(description="Publish selected (approvers only)")
def publish(modeladmin, request, queryset):
    if _is_approver(request):
        queryset.update(status="published")


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


@admin.register(VolunteerProfile)
class VolunteerProfileAdmin(SuperuserOnlyAdmin):
    list_display = ["user", "org", "is_approver"]


class CTAInline(admin.TabularInline):
    model = CTA
    extra = 0


@admin.register(Campaign)
class CampaignAdmin(OrgScopedAdmin):
    feature = "campaigns"
    org_path = "org"
    list_display = ["slug", "title", "org", "status"]
    list_filter = ["status"]
    prepopulated_fields = {"slug": ["title"]}
    inlines = [CTAInline]
    actions = [publish]
    readonly_fields = ["created"]

    def get_changeform_initial_data(self, request):
        org = _user_org(request)
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
