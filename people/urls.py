from django.urls import path

from . import views

app_name = "people"

urlpatterns = [
    path("", views.index, name="index"),
    path("<slug:slug>/", views.person, name="person"),
]
