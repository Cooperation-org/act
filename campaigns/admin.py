"""Org-scoped admin: volunteers see only their org's rows; approvers publish.

Volunteers get is_staff + group 'volunteers' (created by seed command) and a
VolunteerProfile. Superusers see everything.
"""
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

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        org = _user_org(request)
        return qs.filter(**{self.org_path: org}) if org else qs.none()

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.action(description="Publish selected (approvers only)")
def publish(modeladmin, request, queryset):
    if _is_approver(request):
        queryset.update(status="published")


@admin.register(Org)
class OrgAdmin(admin.ModelAdmin):
    list_display = ["slug", "name", "givebutter_account_id"]

    def has_module_permission(self, request):
        return request.user.is_superuser

    has_view_permission = has_add_permission = has_change_permission = has_module_permission


@admin.register(VolunteerProfile)
class VolunteerProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "org", "is_approver"]

    def has_module_permission(self, request):
        return request.user.is_superuser

    has_view_permission = has_add_permission = has_change_permission = has_module_permission


class CTAInline(admin.TabularInline):
    model = CTA
    extra = 0


@admin.register(Campaign)
class CampaignAdmin(OrgScopedAdmin):
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
    org_path = "campaign__org"
    list_display = ["campaign", "relationship", "show_identity", "status", "created"]
    list_filter = ["status"]
    actions = [publish]


@admin.register(Update)
class UpdateAdmin(OrgScopedAdmin):
    org_path = "campaign__org"
    list_display = ["campaign", "date", "status"]
    actions = [publish]


@admin.register(Response)
class ResponseAdmin(OrgScopedAdmin):
    org_path = "cta__campaign__org"
    list_display = ["name", "email", "cta", "handled", "created"]
    list_filter = ["handled", "cta__kind"]
    list_editable = ["handled"]


@admin.register(ShareLink)
class ShareLinkAdmin(OrgScopedAdmin):
    org_path = "campaign__org"
    list_display = ["code", "campaign", "volunteer", "clicks"]
    readonly_fields = ["clicks", "code"]
