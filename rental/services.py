"""
Rental use cases.

The viewset had four copies of the same filter-building block and two copies
of the CSV writer. Both live here now, so a new filter is added once.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Iterator

from django.db.models import Max, Q, QuerySet

import bnbu_constants.constants as constants
from bnbu_constants.utility import (
    clean_price,
    file_to_df,
    normalize_column,
    normalize_df,
    validate_df,
    validate_file_type,
)

from .models import RentalProperty
from .tasks import process_rental_properties_task

logger = logging.getLogger(__name__)

DATE_FORMAT = "%B %d, %Y"
DATE_HINT = 'Use "Month day, year" (e.g., December 3, 2024).'

CSV_COLUMNS = (
    ("Date", lambda p: p.created_at.strftime(DATE_FORMAT) if p.created_at else ""),
    ("Batch Id", lambda p: str(p.batch_id)),
    ("Location", lambda p: p.location or ""),
    ("Rent", lambda p: str(p.rent)),
    ("Bedrooms", lambda p: str(p.no_of_bedrooms)),
    ("Bathrooms", lambda p: str(p.no_of_bathrooms)),
    ("Square Feet", lambda p: str(p.square_feet)),
    ("Link", lambda p: p.property_zillow_link or ""),
    ("Adr", lambda p: str(p.adr)),
    ("Utilities", lambda p: str(p.utilities)),
    ("Estimated Profit", lambda p: str(p.monthly_estimated_profit)),
    ("Estimated Earnings", lambda p: str(p.yearly_projected_revenue)),
    ("Yearly Rent Cost", lambda p: str(p.yearly_rent_cost_util)),
    ("Occupancy Rate", lambda p: str(p.occupancy_rate)),
    ("Zillow Property Status", lambda p: p.property_status),
)

#: Rows pulled from the database at a time while streaming a CSV. Without it
#: the "streaming" response materialised the whole result set first.
CSV_CHUNK_SIZE = 500


class RentalValidationError(ValueError):
    """The request could not be understood."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def start_ingest(*, user, uploaded_file) -> tuple[int, str]:
    """
    Validate a spreadsheet and hand the batch to a worker.

    Returns ``(batch_id, task_id)``. Everything expensive - the AirDNA calls,
    the profit maths, the writes - happens in the worker.
    """
    if not validate_file_type(uploaded_file):
        raise RentalValidationError(
            f"Invalid file type. Allowed: {', '.join(constants.VALID_FILE_EXTENSION)}"
        )

    try:
        frame = file_to_df(uploaded_file)
    except Exception as exc:
        logger.warning("Could not parse uploaded file %s: %s", uploaded_file.name, exc)
        raise RentalValidationError(
            "File could not be read. Upload a valid CSV or Excel file."
        ) from exc

    if frame.empty:
        raise RentalValidationError("File contains no data")

    missing = validate_df(frame)
    if missing:
        raise RentalValidationError(f"Missing columns: {missing}")

    for column in ("Ba", "Br", "Sq. ft."):
        normalize_column(frame, column)
    frame["Price"] = frame["Price"].apply(clean_price)
    frame = normalize_df(frame)

    batch_id = (RentalProperty.objects.aggregate(Max("batch_id"))["batch_id__max"] or 0) + 1
    task = process_rental_properties_task.delay(
        frame.to_json(orient="records"), {"user": user.id, "new_batch_id": batch_id}
    )
    return batch_id, task.id


def build_filter(filters) -> Q:
    """
    Turn the request's filters into one ``Q``.

    ``filtered_list`` and ``download_csv`` accept the same filters - one from a
    JSON body, one from the query string - and each had its own copy of this.
    """
    query = Q()
    if filters.get("min_profit") is not None:
        query &= Q(monthly_estimated_profit__gte=filters["min_profit"])
    if filters.get("max_profit") is not None:
        query &= Q(monthly_estimated_profit__lte=filters["max_profit"])
    if filters.get("status") is not None:
        query &= Q(property_status=filters["status"])
    if filters.get("batch_id") is not None:
        query &= Q(batch_id=filters["batch_id"])

    start_date = filters.get("start_date")
    if start_date:
        query &= Q(created_at__gte=_parse_date(start_date, "start"))

    end_date = filters.get("end_date")
    if end_date:
        # Exclusive end: the caller means "up to and including that day".
        query &= Q(created_at__lte=_parse_date(end_date, "end") + timedelta(days=1))

    return query


def batch_ids(queryset: QuerySet) -> list[int]:
    """The distinct batches in a result set, computed by the database.

    This used to iterate the entire unpaginated queryset in Python purely to
    collect these, loading every matching row into memory before paginating.
    """
    return list(queryset.order_by().values_list("batch_id", flat=True).distinct())


def stream_csv_rows(queryset: QuerySet) -> Iterator[str]:
    """Yield the export one line at a time, a few hundred rows per database read."""
    yield ",".join(name for name, _ in CSV_COLUMNS) + "\n"
    for prop in queryset.iterator(chunk_size=CSV_CHUNK_SIZE):
        yield ",".join(_csv_cell(render(prop)) for _, render in CSV_COLUMNS) + "\n"


def _csv_cell(value: str) -> str:
    text = value if value is not None else ""
    if any(char in text for char in (",", '"', "*", "#", "\n")):
        return '"{}"'.format(text.replace('"', '""'))
    return text


def _parse_date(value, label: str):
    try:
        return datetime.strptime(value, DATE_FORMAT).date()
    except (TypeError, ValueError) as exc:
        raise RentalValidationError(f"Invalid {label} date format. {DATE_HINT}") from exc
