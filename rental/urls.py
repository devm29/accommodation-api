"""Rental-property routes."""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from rental.views import RentalPropertyViewSet

router = DefaultRouter()
router.register(r"rental_properties", RentalPropertyViewSet, basename="rental-properties")

urlpatterns = [
    path("", include(router.urls)),
]
