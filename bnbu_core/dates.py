"""Day-range filters for the search routes."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Optional

from django.utils import timezone

ISO_DAY = "%Y-%m-%d"


class InvalidDay(ValueError):
    """A ``start_date`` or ``end_date`` that does not parse."""

    def __init__(self, label: str, hint: str = "Use YYYY-MM-DD.") -> None:
        self.detail = f"Invalid {label} date format. {hint}"
        super().__init__(self.detail)


def parse_day(value, label: str, fmt: str = ISO_DAY, hint: str = "Use YYYY-MM-DD.") -> date:
    try:
        return datetime.strptime(value, fmt).date()
    except (TypeError, ValueError) as exc:
        raise InvalidDay(label, hint) from exc


def start_of_day(day: date) -> datetime:
    """Midnight at the start of ``day`` in the active time zone."""
    return timezone.make_aware(datetime.combine(day, time.min))


def day_range(start: Optional[date], end: Optional[date], field: str = "created_at") -> dict:
    """
    Lookup kwargs for "from the start of ``start`` through the whole of ``end``".

    The end bound is exclusive at the following midnight. The searches used
    ``created_at__lte=end + 1 day`` on a bare date, which also matched rows
    stamped exactly at that midnight and compared a naive date against an
    aware column.
    """
    lookups = {}
    if start is not None:
        lookups[f"{field}__gte"] = start_of_day(start)
    if end is not None:
        lookups[f"{field}__lt"] = start_of_day(end + timedelta(days=1))
    return lookups


def filter_by_days(queryset, params, field: str = "created_at"):
    """Apply ``start_date``/``end_date`` query parameters (``YYYY-MM-DD``)."""
    start = params.get("start_date")
    end = params.get("end_date")
    return queryset.filter(
        **day_range(
            parse_day(start, "start") if start else None,
            parse_day(end, "end") if end else None,
            field,
        )
    )
