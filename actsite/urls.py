from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("letters/", include("letters.urls")),
    path("", include("campaigns.urls")),
]
if settings.LINKEDTRUST_SSO_ENABLED:
    urlpatterns.insert(1, path("api/v1/auth/linkedtrust/", include("linkedtrust_auth.urls")))
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
