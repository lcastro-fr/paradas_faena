from django.urls import path

from monitor.api import views

app_name = "monitor"

urlpatterns = [
    path("", views.index, name="index"),
    path("api/snapshot", views.api_snapshot, name="snapshot"),
    path("stream", views.stream, name="stream"),
    path("healthz", views.healthz, name="healthz"),
]
