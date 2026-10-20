from django.urls import path

from . import views

app_name = "artifacts"

urlpatterns = [
    path("kit/", views.home, name="home"),
    path("signin/", views.signin, name="signin"),
    path("start/", views.start, name="start"),
    path("org/<int:pk>/", views.switch_org, name="switch_org"),
    path("ways/", views.ways, name="ways"),
    path("ways/link/", views.add_link, name="add_link"),
    path("ways/drive/", views.add_drive, name="add_drive"),
    path("ways/claims/", views.add_claims, name="add_claims"),
    path("ways/check/<int:pk>/", views.check_source, name="check_source"),
    path("send/", views.send, name="send"),
    path("share/", views.share_target, name="share"),
    path("library/", views.library, name="library"),
    path("library/<int:pk>/", views.artifact, name="artifact"),
    path("labels/", views.labels, name="labels"),
    path("collections/", views.collections, name="collections"),
    path("collections/<int:pk>/", views.collection, name="collection"),
    path("stories/", views.stories, name="stories"),
    path("stories/<slug:slug>/", views.story, name="story"),
    path("stories/<slug:slug>/edit/", views.story_edit, name="story_edit"),
    path("manifest.webmanifest", views.manifest, name="manifest"),
    path("sw.js", views.service_worker, name="sw"),
]
