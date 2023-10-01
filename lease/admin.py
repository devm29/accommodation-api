from django.contrib import admin

from .models import Document, Lease


class DocumentInline(admin.TabularInline):
    model = Document
    extra = 0
    fields = ("name", "version", "status", "uploaded_at")
    readonly_fields = ("version", "uploaded_at")
    show_change_link = True


@admin.register(Lease)
class LeaseAdmin(admin.ModelAdmin):
    list_display = ("address", "city", "state", "status", "user", "created_at")
    list_filter = ("status", "state")
    search_fields = ("address1", "address2", "city")
    inlines = [DocumentInline]


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    """
    The review is the interesting column here: the verdict and how sure the
    model was about it, side by side.
    """

    list_display = ("name", "lease", "version", "status", "verdict_confidence", "uploaded_at")
    list_filter = ("status",)
    search_fields = ("name",)
    readonly_fields = ("gpt_response", "analysis", "chat_history")

    @admin.display(description="Confidence")
    def verdict_confidence(self, obj):
        analysis = obj.analysis if isinstance(obj.analysis, dict) else {}
        confidence = analysis.get("confidence")
        return f"{confidence:.0%}" if isinstance(confidence, (int, float)) else "-"
