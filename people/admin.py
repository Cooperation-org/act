from django.contrib import admin, messages

from campaigns.admin import OrgScopedAdmin, _first_org, publishable

from .models import Person


@admin.action(description="Publish selected (approvers only; consent must be on record)")
def publish_people(modeladmin, request, queryset):
    allowed = publishable(modeladmin, request, queryset)
    no_consent = allowed.filter(consent_on_record=False).count()
    n = allowed.filter(consent_on_record=True).update(status="published")
    if n:
        messages.success(request, f"Published {n}.")
    if no_consent:
        messages.warning(request, f"{no_consent} not published: consent is not on record.")
    if queryset.count() - allowed.count():
        messages.warning(request, "Some rows skipped: you are not an approver for that org.")


@admin.register(Person)
class PersonAdmin(OrgScopedAdmin):
    feature = "people"
    org_path = "org"
    list_display = ["name", "role", "org", "consent_on_record", "status", "sort"]
    list_filter = ["status", "org", "consent_on_record"]
    list_editable = ["sort"]
    prepopulated_fields = {"slug": ["name"]}
    actions = [publish_people]
    readonly_fields = ["created"]
    fieldsets = [
        (None, {"fields": ["org", "name", "slug", "role", "photo", "sort"]}),
        ("Own words (Markdown)", {"fields": ["bio", "links", "simpletip_receiver"]}),
        ("Publishing", {"fields": ["consent_on_record", "status", "user", "created"]}),
    ]

    def get_changeform_initial_data(self, request):
        org = _first_org(request)
        return {"org": org.pk} if org else {}

    def save_model(self, request, obj, form, change):
        if obj.status == "published" and not obj.consent_on_record:
            obj.status = "pending"
            messages.warning(request, "Kept as pending: consent is not on record.")
        super().save_model(request, obj, form, change)
