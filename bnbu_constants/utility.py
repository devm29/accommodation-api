"""AirDNA client, spreadsheet parsing and the property profit maths."""

import logging
import math
from typing import NamedTuple, Optional

import pandas as pd
import requests
from django.conf import settings

import bnbu_constants.constants as constants

logger = logging.getLogger(__name__)


class ProfitBreakdown(NamedTuple):
    """
    Indexable, so the existing `[-1]` and `[2]` call sites keep working -- but
    always four fields. The previous version returned three values on its
    failure path and four on its success path.
    """

    utilities: Optional[float]
    annual_revenue: Optional[float]
    yearly_rent_cost_util: Optional[float]
    monthly_estimated_profit: Optional[float]


EMPTY_BREAKDOWN = ProfitBreakdown(None, None, None, None)


def _is_missing(value) -> bool:
    """
    True for None, 0 and NaN alike.

    NaN is the one that mattered: `not float("nan")` is False, so a property the
    AirDNA lookup had no data for sailed past the old guard, produced a NaN
    profit, and was written out as a decision rather than as missing data.
    """
    if value is None:
        return True
    try:
        if math.isnan(float(value)):
            return True
    except (TypeError, ValueError):
        return True
    return not value


def _is_unknown(value) -> bool:
    """True only for None and NaN. A profit of exactly zero is a real result."""
    if value is None:
        return True
    try:
        return math.isnan(float(value))
    except (TypeError, ValueError):
        return True


def validate_file_type(file):
    if not file.name.endswith(tuple(constants.VALID_FILE_EXTENSION)):
        return False
    return True


def file_to_df(file):
    if file.name.endswith(".csv"):
        df = pd.read_csv(file)
    else:
        df = pd.read_excel(file)
    return df


def validate_df(df):
    missing_cols = []
    if any(col not in df.columns for col in constants.REQUIRED_COLS):
        missing_cols = set(constants.REQUIRED_COLS) - set(df.columns)
    return missing_cols


def normalize_column(df, col_name):
    # df[col_name] = df[col_name].astype(str).str.extract('(\d+)').astype(float)
    df[col_name] = df[col_name].astype(str).str.extract(r"(\d+)").astype(float)


def normalize_df(df):
    df = df.dropna(subset=constants.IMP_COLS)
    return df


def clean_price(price):
    try:
        return int(str(price).replace("$", "").replace("/mo", "").replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def process_airdna_api(bulk_queries, retry_count=5):
    """Price a batch of addresses through AirDNA, retrying transient failures."""
    if not settings.AIRDNA_URL:
        raise ValueError("AIRDNA_URL is not configured.")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.AIRDNA_API_KEY}",
    }
    payload = {"queries": bulk_queries}

    last_error = None
    for attempt in range(retry_count):
        try:
            response = requests.post(settings.AIRDNA_URL, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            results = response.json()["payload"]["results"]
            if not results:
                raise ValueError("No results returned from API.")
            return results
        # The loop used to catch RequestException only, so the ValueError it
        # raises itself -- and a KeyError from an unexpected response shape --
        # escaped on the first attempt without ever being retried.
        except (requests.exceptions.RequestException, ValueError, KeyError) as exc:
            last_error = exc
            logger.warning(
                "AirDNA request failed (attempt %d of %d): %s", attempt + 1, retry_count, exc
            )

    raise ValueError(f"AirDNA API request failed after {retry_count} attempts") from last_error


def calculate_utilities(no_of_bedroom):
    return no_of_bedroom * 2000


def calculate_monthly_profit(annual_revenue, monthly_rent, no_of_bedroom):
    """
    Returns a ProfitBreakdown. Every field is None when any input is missing,
    so a property that could not be evaluated stays visibly unevaluated.
    """
    if _is_missing(annual_revenue) or _is_missing(monthly_rent) or _is_missing(no_of_bedroom):
        return EMPTY_BREAKDOWN

    utilities = calculate_utilities(no_of_bedroom)
    yearly_rent_cost_util = (monthly_rent * 12) + utilities
    monthly_estimated_profit = (annual_revenue - yearly_rent_cost_util) / 12
    return ProfitBreakdown(
        utilities,
        annual_revenue,
        yearly_rent_cost_util,
        round(monthly_estimated_profit, 2),
    )


def determine_property_status(no_of_bedrooms, monthly_estimated_profit):
    """Determine the property status based on number of bedrooms and monthly profit."""
    # NaN reached here whenever AirDNA had no data for the address, and
    # `nan >= 1000` is False, so the property was reported Rejected -- a verdict
    # on the investment -- when nothing had actually been worked out about it.
    if _is_unknown(monthly_estimated_profit) or _is_unknown(no_of_bedrooms):
        return constants.ERROR

    if no_of_bedrooms == 1 and monthly_estimated_profit >= 1000:
        return constants.APPROVED
    elif no_of_bedrooms == 2 and monthly_estimated_profit >= 1500:
        return constants.APPROVED
    elif no_of_bedrooms >= 3 and monthly_estimated_profit >= 2000:
        return constants.APPROVED
    else:
        return constants.REJECTED
