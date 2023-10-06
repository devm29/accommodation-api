"""Short-term-let regulation routes."""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import RegulationsViewSet

router = DefaultRouter()
router.register(r"regulations", RegulationsViewSet, basename="regulations")

urlpatterns = [
    path("", include(router.urls)),
]
