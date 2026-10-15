from django.contrib import admin, messages

from campaigns.admin import OrgScopedAdmin

from . import spouts
from .models import Artifact, Collection, Source, Story, StoryBlock, Tag


@admin.action(description="Check now (Drive folder or LinkedClaims)")
def check_now(modeladmin, request, queryset):
    for s in queryset:
        msg = spouts.check(s)
        if msg:
            messages.info(request, f"{s}: {msg}")


@admin.register(Source)
class SourceAdmin(OrgScopedAdmin):
    feature = "artifacts"
    list_display = ("org", "kind", "label", "last_checked", "last_error")
    list_filter = ("kind",)
    actions = [check_now]


@admin.register(Tag)
class TagAdmin(OrgScopedAdmin):
    feature = "artifacts"
    list_display = ("name", "org")


@admin.register(Artifact)
class ArtifactAdmin(OrgScopedAdmin):
    feature = "artifacts"
    list_display = ("__str__", "org", "kind", "sent_by", "happened_at", "received_at", "status")
    list_filter = ("kind", "status", "source__kind")
    search_fields = ("words", "title", "filename", "sent_by", "about")
    readonly_fields = ("sha256", "size", "content_type", "received_at", "source_ref", "source_url")
    filter_horizontal = ("tags",)


@admin.register(Collection)
class CollectionAdmin(OrgScopedAdmin):
    feature = "artifacts"
    list_display = ("name", "org", "created")
    filter_horizontal = ("artifacts",)


class StoryBlockInline(admin.TabularInline):
    model = StoryBlock
    extra = 0
    raw_id_fields = ("artifact",)


@admin.register(Story)
class StoryAdmin(OrgScopedAdmin):
    feature = "artifacts"
    list_display = ("title", "org", "status", "published_at", "updated")
    prepopulated_fields = {"slug": ("title",)}
    inlines = [StoryBlockInline]
