from django.contrib import admin

from .models import Regulations


@admin.register(Regulations)
class RegulationsAdmin(admin.ModelAdmin):
    list_display = ("search", "status", "analysis_state", "source", "user", "created_at")
    list_filter = ("status", "analysis_state", "source")
    search_fields = ("search",)
    readonly_fields = ("gpt_response", "chat_history")
