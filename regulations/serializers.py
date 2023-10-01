from rest_framework import serializers

from .models import Regulations


class RegulationsSerializer(serializers.ModelSerializer):
    class Meta:
        model = Regulations
        fields = [
            "id",
            "date",
            "search",
            "status",
            "gpt_response",
            "chat_history",
            "analysis_state",
            "source",
        ]
        read_only_fields = ["status", "gpt_response", "chat_history", "analysis_state", "source"]

    def validate_search(self, value):
        cleaned = (value or "").strip()
        if not cleaned:
            raise serializers.ValidationError("The search field is required.")
        return cleaned


class GPTChatSerializer(serializers.Serializer):
    """
    The chat request body.

    ``regulation_id`` duplicates the id already in the URL, and the view used
    to trust the body and ignore the URL. It stays optional so the existing
    frontend keeps working, but the URL is now authoritative and a body that
    disagrees with it is rejected rather than silently preferred.
    """

    message = serializers.CharField(
        required=True,
        allow_blank=False,
        help_text="Your question about this location's short-term-rental rules.",
    )
    regulation_id = serializers.IntegerField(
        required=False,
        help_text="Optional and deprecated: must match the id in the URL.",
    )
