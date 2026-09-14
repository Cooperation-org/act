from django.conf import settings
from django.contrib.syndication.views import Feed

from actsite.markup import render as md
from campaigns.context import site_org

from .views import published


class PostFeed(Feed):
    def title(self):
        org = site_org()
        return f"{org.name}: blog" if org else "Blog"

    def link(self):
        return f"{settings.PUBLIC_URL}/blog/"

    description = "Posts"

    def items(self):
        return published()[:30]

    def item_title(self, item):
        return item.title

    def item_description(self, item):
        return item.summary or md(item.body)

    def item_link(self, item):
        return f"{settings.PUBLIC_URL}/blog/{item.slug}/"

    def item_pubdate(self, item):
        return item.published_at
