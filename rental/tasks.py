"""
Background work for the rental app.

One uploaded spreadsheet becomes one task: price each address through AirDNA
in chunks, work out the profit, and write the rows. The chunking matters -
AirDNA is called once per 25 properties rather than once per property - and so
does what happens when a chunk fails, which is where this used to go wrong.
"""

from __future__ import absolute_import, unicode_literals

import logging
from datetime import datetime
from io import StringIO

import pandas as pd
import requests
from celery import shared_task
from django.conf import settings
from tqdm import tqdm

import bnbu_constants.constants as constants
from bnbu_constants.utility import (
    calculate_monthly_profit,
    calculate_utilities,
    determine_property_status,
    process_airdna_api,
)
from rental.models import RentalProperty

logger = logging.getLogger(__name__)

CHUNK_SIZE = 25

CLICKUP_STATUS_OPTIONS = {
    "Approved": "APPROVED",
    "Rejected": "REJECTED",
    "Call Back": "CALL_BACK",
    "Owner Approval": "OWNER_APPROVAL",
    "On Hold": "ON_HOLD",
    "SEE NOTES": "SEE_NOTES",
    "FOR CHI ONLY": "FOR_CHI_ONLY",
}


@shared_task(bind=True)
def process_rental_properties_task(self, cleaned_df_json, context):
    """Price and store one uploaded batch."""
    batch_id = context["new_batch_id"]
    user_id = context["user"]

    try:
        frame = pd.read_json(StringIO(cleaned_df_json))
    except Exception:
        logger.exception("Could not load the uploaded batch %s from JSON.", batch_id)
        raise

    total_rows = len(frame)
    logger.info("Loaded batch %s with %d rows.", batch_id, total_rows)

    results = []
    skipped_rows = 0
    failed_chunks = []

    with tqdm(total=total_rows, desc="Processing", unit="row") as progress:
        for start in range(0, total_rows, CHUNK_SIZE):
            chunk = frame.iloc[start : start + CHUNK_SIZE]
            try:
                priced = _price_chunk(chunk)
            except Exception as exc:
                # A failed chunk used to `continue` with nothing but a log
                # line, and the task still finished by declaring 100% and
                # "completed successfully".
                logger.error(
                    "AirDNA lookup failed for rows %d-%d: %s", start, start + len(chunk), exc
                )
                skipped_rows += len(chunk)
                failed_chunks.append({"start": start, "rows": len(chunk), "error": str(exc)})
                progress.update(len(chunk))
                continue

            _store_chunk(priced, user_id=user_id, batch_id=batch_id)
            results.append(priced)

            progress.update(len(chunk))
            self.update_state(
                state="PROGRESS",
                meta={
                    "progress": int(progress.n / total_rows * 100) if total_rows else 100,
                    "message": f"Processed {progress.n} of {total_rows} rows",
                },
            )

    self.update_state(state="PROGRESS", meta=_summary(total_rows, skipped_rows, failed_chunks))
    logger.info("Finished batch %s.", batch_id)

    # pd.concat([]) raises "No objects to concatenate", so a run in which every
    # AirDNA chunk failed died here with a confusing error instead of reporting
    # that it had produced nothing.
    if not results:
        logger.warning("No chunks were processed for batch %s.", batch_id)
        return "[]"
    return pd.concat(results).to_json(orient="records")


def _price_chunk(chunk):
    """Ask AirDNA about one chunk and attach the derived numbers."""
    queries = [
        {
            "address": row.Location,
            "bedrooms": row.Br,
            "bathrooms": row.Ba,
            "accommodates": row.Br * 2 if row.Br else None,
            "currency": constants.CURRENCY_USD,
        }
        for row in chunk.itertuples(index=False)
    ]
    response = process_airdna_api(queries)

    estimates = pd.DataFrame(
        [
            {
                "ADR": entry["stats"]["future"]["summary"].get("adr"),
                "Occupancy": entry["stats"]["future"]["summary"].get("occupancy"),
                "Revenue": entry["stats"]["future"]["summary"].get("revenue"),
                "Location": entry["details"]["address"],
            }
            for entry in response
        ]
    )

    merged = pd.merge(chunk, estimates, on="Location", how="left")
    merged["utilities"] = merged["Br"].apply(calculate_utilities)
    merged["monthly_estimated_profit"] = merged.apply(
        lambda row: calculate_monthly_profit(row.Revenue, row.Price, row.Br)[-1], axis=1
    )
    merged["property_status"] = merged.apply(
        lambda row: determine_property_status(row.Br, row.monthly_estimated_profit), axis=1
    )
    return merged


def _store_chunk(priced, *, user_id, batch_id):
    for _, row in priced.iterrows():
        try:
            _store_row(row, user_id=user_id, batch_id=batch_id)
        except Exception as exc:
            logger.error("Could not save %s: %s", row.get("Location"), exc, exc_info=True)


def _store_row(row, *, user_id, batch_id):
    square_feet = row.get("Sq. ft.")
    if pd.isna(square_feet):
        logger.warning("Skipping %s: no square footage.", row["Location"])
        return

    prop = RentalProperty(
        user_id=user_id,
        location=row["Location"],
        rent=_number(row.get("Price"), default=0),
        no_of_bedrooms=_number(row.get("Br"), default=0),
        no_of_bathrooms=_number(row.get("Ba"), default=0),
        square_feet=int(square_feet),
        property_zillow_link=row["Link"],
        adr=_number(row.get("ADR"), default=0),
        occupancy_rate=_number(row.get("Occupancy"), default=0),
        utilities=_number(row.get("utilities")),
        yearly_projected_revenue=_number(row.get("Revenue"), default=0),
        # Storing 0 here made an unevaluable property look like it had been
        # worked out and come to break even. The column is nullable.
        monthly_estimated_profit=_number(row.get("monthly_estimated_profit")),
        batch_id=batch_id,
        property_status=row["property_status"],
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    prop.yearly_rent_cost_util = calculate_monthly_profit(
        prop.yearly_projected_revenue, prop.rent, prop.no_of_bedrooms
    )[2]
    prop.save()

    if prop.property_status == constants.APPROVED:
        _push_to_clickup(prop)


def _number(value, default=None):
    return default if value is None or pd.isna(value) else value


def _summary(total_rows, skipped_rows, failed_chunks):
    if failed_chunks:
        message = (
            f"Completed with gaps: {skipped_rows} of {total_rows} rows could not be "
            f"priced because the AirDNA lookup failed for {len(failed_chunks)} batch(es)."
        )
    else:
        message = "Task completed successfully"
    return {
        "progress": 100,
        "message": message,
        "skipped_rows": skipped_rows,
        "failed_chunks": failed_chunks,
    }


def _push_to_clickup(prop):
    """
    Optional integration: approved properties land on a ClickUp board.

    Without CLICKUP_URL configured this used to call ``requests.post(None,...)``
    for every approved property, and the resulting error was swallowed as
    "Error saving RentalProperty" - which it was not; the row was already saved.
    """
    if not settings.CLICKUP_URL:
        logger.info("CLICKUP_URL is not configured; skipping upload for %s", prop.location)
        return
    try:
        upload_properties_to_clickup(prop.location, prop.property_zillow_link, prop.property_status)
    except Exception as exc:
        logger.error("ClickUp upload failed for %s: %s", prop.location, exc)


def upload_properties_to_clickup(task_name, zillow_link, property_status):
    """Create one ClickUp task for an approved property."""
    # The status option ids are ClickUp's own UUIDs and differ per board, so
    # they are read from the environment rather than hardcoded.
    status_option = None
    env_name = CLICKUP_STATUS_OPTIONS.get(property_status)
    if env_name:
        status_option = getattr(settings, env_name, None)

    payload = {
        "name": task_name,
        "status": "to do",
        "custom_fields": [
            {"id": settings.ZILLOW_CUSTOM_ID, "value": zillow_link},
            {"id": settings.PROPERTY_STATUS_CUSTOM_ID, "value": status_option},
        ],
    }
    logger.debug("Payload sent to ClickUp: %s", payload)

    try:
        response = requests.post(
            settings.CLICKUP_URL,
            headers={
                "content-type": "application/json",
                "Authorization": settings.ACCESS_TOKEN,
            },
            json=payload,
            params={"custom_task_ids": True, "team_id": settings.TEAM_ID},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.JSONDecodeError as exc:
        raise ValueError(f"ClickUp returned a non-JSON response: {response.text}") from exc
    except requests.exceptions.RequestException as exc:
        raise ValueError(f"Request to ClickUp failed: {exc}") from exc
