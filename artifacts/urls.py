from django.conf import settings
from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "artifacts"

# Story Kit's home is the site root when it is the only app here; next to campaigns
# (which owns "/") it lives at kit/.
if "campaigns" in settings.ENABLED_APPS:
    home = [path("kit/", views.home, name="home")]
else:
    home = [path("", views.home, name="home"),
            path("kit/", RedirectView.as_view(pattern_name="artifacts:home", permanent=True))]

urlpatterns = home + [
    path("demo/", views.demo_open, name="demo"),
    path("demo/start-your-own/", views.demo_leave, name="demo_leave"),
    path("signin/", views.signin, name="signin"),
    path("start/", views.start, name="start"),
    path("org/<int:pk>/", views.switch_org, name="switch_org"),
    path("ways/", views.ways, name="ways"),
    path("ways/link/", views.add_link, name="add_link"),
    path("ways/number/", views.add_number, name="add_number"),
    path("ways/drive/", views.add_drive, name="add_drive"),
    path("ways/claims/", views.add_claims, name="add_claims"),
    path("ways/check/<int:pk>/", views.check_source, name="check_source"),
    path("send/", views.send, name="send"),
    path("share/", views.share_target, name="share"),
    path("library/", views.library, name="library"),
    path("library/<int:pk>/", views.artifact, name="artifact"),
    path("library/<int:pk>/quick/", views.artifact_quick, name="artifact_quick"),
    path("labels/", views.labels, name="labels"),
    path("collections/", views.collections, name="collections"),
    path("collections/<int:pk>/", views.collection, name="collection"),
    path("stories/", views.stories, name="stories"),
    path("stories/<slug:slug>/", views.story, name="story"),
    path("stories/<slug:slug>/edit/", views.story_edit, name="story_edit"),
    path("files/<path:path>", views.media_file, name="media_file"),
    path("manifest.webmanifest", views.manifest, name="manifest"),
    path("sw.js", views.service_worker, name="sw"),
]
