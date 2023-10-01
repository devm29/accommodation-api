from django.db import models

import bnbu_constants.constants as constants


class RentalProperty(models.Model):
    """
    One listing, priced.

    The owner is a plain integer column rather than a foreign key - that is
    how the table was built and the data is live - so it needs its index
    declared explicitly, and object permissions compare ids rather than
    instances.
    """

    user_id = models.IntegerField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, blank=True, null=True)
    location = models.CharField(max_length=255, blank=True, null=True)
    rent = models.IntegerField(null=True, blank=True)
    no_of_bedrooms = models.IntegerField(null=True, blank=True)
    no_of_bathrooms = models.IntegerField(null=True, blank=True)
    square_feet = models.IntegerField(null=True, blank=True)
    utilities = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    adr = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    occupancy_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    property_zillow_link = models.URLField()
    property_status = models.CharField(
        max_length=20, choices=constants.RENTAL_PROPERY_STATUS_CHOICES, default=constants.PENDING
    )
    yearly_rent_cost_util = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )

    # Will be extracted from AIR DNA API
    yearly_projected_revenue = models.IntegerField(null=True, blank=True)
    monthly_estimated_profit = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    batch_id = models.IntegerField(db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "rental properties"
        indexes = [
            # Every list, filter and export route is "this user's rows, newest
            # first"; the profit filter runs on top of that.
            models.Index(fields=["user_id", "-created_at"], name="rental_user_recent_idx"),
            models.Index(
                fields=["property_status", "monthly_estimated_profit"],
                name="rental_status_profit_idx",
            ),
        ]

    def __str__(self):
        return f"{self.location} - {self.property_status}"
