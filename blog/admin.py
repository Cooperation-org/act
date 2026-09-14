from django.conf import settings
from django.contrib import admin, messages
from django.utils import timezone
from django.utils.html import format_html

from campaigns.admin import OrgScopedAdmin, _first_org, publishable

from .models import Post


@admin.action(description="Publish selected (approvers only)")
def publish_posts(modeladmin, request, queryset):
    allowed = publishable(modeladmin, request, queryset)
    now = timezone.now()
    n = allowed.exclude(status="published").update(status="published")
    allowed.filter(published_at__isnull=True).update(published_at=now)
    if n:
        messages.success(request, f"Published {n}.")
    if queryset.count() - allowed.count():
        messages.warning(request, "Some rows skipped: you are not an approver for that org.")


@admin.register(Post)
class PostAdmin(OrgScopedAdmin):
    feature = "blog"
    org_path = "org"
    list_display = ["title", "org", "author", "status", "published_at", "preview"]
    list_filter = ["status", "org"]
    prepopulated_fields = {"slug": ["title"]}
    actions = [publish_posts]
    readonly_fields = ["created", "preview"]

    @admin.display(description="Review link")
    def preview(self, obj):
        if not obj.pk:
            return ""
        url = f"{settings.PUBLIC_URL}{obj.preview_path}"
        return format_html('<a href="{}" target="_blank">{}</a>', url, "open draft" if obj.status != "published" else "open")
    fieldsets = [
        (None, {"fields": ["org", "title", "slug", "author", "photo"]}),
        ("Text (Markdown)", {"fields": ["summary", "body", "source_url"]}),
        ("Publishing", {"fields": ["status", "published_at", "preview", "created"]}),
    ]

    def get_changeform_initial_data(self, request):
        org = _first_org(request)
        return {"org": org.pk} if org else {}

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "author" and not request.user.is_superuser:
            from campaigns.admin import user_orgs
            kwargs["queryset"] = db_field.remote_field.model.objects.filter(org__in=user_orgs(request.user))
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
