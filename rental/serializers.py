"""Serializers for rental properties."""

from rest_framework import serializers

from rental.models import RentalProperty


class RentalPropertySerializer(serializers.ModelSerializer):
    created_at_formatted = serializers.SerializerMethodField()

    class Meta:
        model = RentalProperty
        fields = "__all__"
        # The owner comes from the request. Writable, it let a caller create
        # rows in someone else's account or hand their own rows away.
        read_only_fields = ["user_id"]

    def get_created_at_formatted(self, obj):
        if obj.created_at:
            return obj.created_at.strftime("%B %d, %Y")
        return None
