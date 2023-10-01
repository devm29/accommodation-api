from django.contrib import admin

from rental.models import RentalProperty


@admin.register(RentalProperty)
class RentalPropertyAdmin(admin.ModelAdmin):
    """
    The columns that matter when reviewing a batch. Showing the profit and the
    status side by side is what makes an unevaluable property obvious: it has
    a status of Error and no profit, rather than a profit of 0 and a verdict.
    """

    list_display = (
        "location",
        "no_of_bedrooms",
        "rent",
        "yearly_projected_revenue",
        "monthly_estimated_profit",
        "property_status",
        "batch_id",
    )
    list_filter = ("property_status", "no_of_bedrooms", "batch_id")
    search_fields = ("location",)
    ordering = ("-id",)
