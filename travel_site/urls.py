from main import views as main_views
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from main import impersonation as imp_views   # ← add this import


urlpatterns = [
    path("impersonate/<int:user_id>/", imp_views.impersonate, name="impersonate"),
    path("impersonate/release/", imp_views.release_impersonation, name="impersonate_release"),
    path("hijack/", include("hijack.urls")),
    path('admin/', admin.site.urls),
    path('captcha/', include('captcha.urls')),
    path("i18n/", include("django.conf.urls.i18n")),  # for set_language
    path('', include('main.urls')),
    path("captcha/", include("captcha.urls")),

]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)