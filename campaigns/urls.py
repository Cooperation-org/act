from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("why/", views.why, name="why"),
    path("c/<slug:slug>/", views.campaign, name="campaign"),
    path("c/<slug:slug>/act/<int:cta_id>/", views.respond, name="respond"),
    path("c/<slug:slug>/testimony/", views.add_testimony, name="testimony"),
    path("c/<slug:slug>/share/", views.my_share_link, name="share"),
    path("c/<slug:slug>/card/<str:kind>.png", views.share_card, name="card"),
    path("s/<str:code>/", views.share_redirect, name="share_redirect"),
]
