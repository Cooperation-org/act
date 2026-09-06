from django.urls import path

from . import views

app_name = "letters"

urlpatterns = [
    path("", views.index, name="index"),
    path("<slug:slug>/", views.letter, name="letter"),
    path("<slug:slug>.pdf", views.pdf, name="pdf"),
    path("<slug:slug>/confirm/<str:token>/", views.confirm, name="confirm"),
]
