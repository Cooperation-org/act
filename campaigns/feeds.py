from django.conf import settings
from django.contrib.syndication.views import Feed

from .context import site_org


class UpdatesFeed(Feed):
    """Campaign updates and blog posts as one RSS feed."""

    def title(self):
        org = site_org()
        return f"{org.name}: updates" if org else "Updates"

    def link(self):
        return f"{settings.PUBLIC_URL}/updates/"

    description = "Updates, receipts and posts"

    def items(self):
        from .views import stream
        return stream(site_org())[:30]

    def item_title(self, item):
        if item["kind"] == "post":
            return item["post"].title
        return f"{item['campaign'].title}, {item['date']}"

    def item_description(self, item):
        return item["text"]

    def item_link(self, item):
        if item["kind"] == "post":
            return f"{settings.PUBLIC_URL}/blog/{item['post'].slug}/"
        return f"{settings.PUBLIC_URL}/c/{item['campaign'].slug}/"

    def item_guid(self, item):
        if item["kind"] == "post":
            return f"post-{item['post'].pk}"
        return f"update-{item['campaign'].slug}-{item['date']}"
