from django.urls import path

from . import views
from .feeds import PostFeed

app_name = "blog"

urlpatterns = [
    path("", views.index, name="index"),
    path("feed/", PostFeed(), name="feed"),
    path("<slug:slug>/", views.post, name="post"),
]
