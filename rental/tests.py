"""
Tests for the property investment maths.

These are the numbers the product exists to produce: given a monthly rent from
a listing and a revenue estimate from AirDNA, decide whether a short-term
rental is worth taking on. Every case below corresponds to something the
original code got wrong.
"""

import math
from unittest import mock

import pandas as pd
import requests
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, APITestCase

import bnbu_constants.constants as constants
from bnbu_constants.utility import (
    ProfitBreakdown,
    calculate_monthly_profit,
    calculate_utilities,
    clean_price,
    determine_property_status,
    normalize_column,
    normalize_df,
    process_airdna_api,
    validate_df,
    validate_file_type,
)
from bnbu_core.pagination import DefaultPagination
from rental.models import RentalProperty

CustomUser = get_user_model()

NAN = float("nan")


def make_property(user_id, **kwargs):
    """A RentalProperty with the non-nullable columns filled in."""
    kwargs.setdefault("batch_id", 1)
    kwargs.setdefault("property_zillow_link", "http://example.com/listing")
    kwargs.setdefault("location", "1 Main St")
    return RentalProperty.objects.create(user_id=user_id, **kwargs)


class CalculateMonthlyProfitTests(SimpleTestCase):
    def test_computes_the_worked_example(self):
        # A 2-bed at $2,000/mo rent, estimated to bring in $90,000 a year.
        # Utilities are $2,000 per bedroom per year, so $4,000.
        # Yearly cost = 2000*12 + 4000 = 28,000.  (90,000 - 28,000) / 12
        result = calculate_monthly_profit(90_000, 2_000, 2)
        self.assertEqual(result.utilities, 4_000)
        self.assertEqual(result.yearly_rent_cost_util, 28_000)
        self.assertAlmostEqual(result.monthly_estimated_profit, 5_166.67, places=2)

    def test_returns_the_same_number_of_values_on_both_paths(self):
        # The failure path used to return three values and the success path
        # four. Call sites indexed [-1] and [2] and happened to survive it.
        full = calculate_monthly_profit(90_000, 2_000, 2)
        empty = calculate_monthly_profit(None, 2_000, 2)
        self.assertEqual(len(full), len(empty))
        self.assertEqual(len(full), 4)
        self.assertIsInstance(empty, ProfitBreakdown)

    def test_nan_revenue_is_treated_as_missing(self):
        # `not float("nan")` is False, so NaN sailed past the old guard and
        # produced a NaN profit that was then written to the database.
        result = calculate_monthly_profit(NAN, 2_000, 2)
        self.assertIsNone(result.monthly_estimated_profit)

    def test_missing_inputs_give_nothing_back(self):
        for revenue, rent, beds in [
            (None, 2_000, 2),
            (90_000, None, 2),
            (90_000, 2_000, None),
            (NAN, 2_000, 2),
            (90_000, NAN, 2),
            (90_000, 2_000, NAN),
        ]:
            with self.subTest(revenue=revenue, rent=rent, beds=beds):
                result = calculate_monthly_profit(revenue, rent, beds)
                self.assertIsNone(result.monthly_estimated_profit)
                self.assertIsNone(result.yearly_rent_cost_util)

    def test_never_returns_nan(self):
        for revenue in [NAN, None, 0, 90_000]:
            with self.subTest(revenue=revenue):
                profit = calculate_monthly_profit(revenue, 2_000, 2).monthly_estimated_profit
                self.assertFalse(
                    profit is not None and math.isnan(profit),
                    "a NaN reaching the database is indistinguishable from a real figure",
                )


class DeterminePropertyStatusTests(SimpleTestCase):
    def test_thresholds_rise_with_bedroom_count(self):
        self.assertEqual(determine_property_status(1, 1_000), constants.APPROVED)
        self.assertEqual(determine_property_status(1, 999), constants.REJECTED)
        self.assertEqual(determine_property_status(2, 1_500), constants.APPROVED)
        self.assertEqual(determine_property_status(2, 1_499), constants.REJECTED)
        self.assertEqual(determine_property_status(3, 2_000), constants.APPROVED)
        self.assertEqual(determine_property_status(4, 2_000), constants.APPROVED)
        self.assertEqual(determine_property_status(3, 1_999), constants.REJECTED)

    def test_unknown_profit_is_an_error_not_a_rejection(self):
        # This is the one that mattered. When AirDNA had no data for an
        # address the left join produced NaN, `nan >= 1500` was False, and the
        # property came out "Rejected" -- a verdict on the investment, from a
        # calculation that never happened. "Error" already existed as a status.
        self.assertEqual(determine_property_status(2, NAN), constants.ERROR)
        self.assertEqual(determine_property_status(2, None), constants.ERROR)

    def test_a_real_loss_is_still_a_rejection(self):
        self.assertEqual(determine_property_status(2, -800), constants.REJECTED)

    def test_zero_profit_is_a_real_result(self):
        # Breaking even is a number, not missing data.
        self.assertEqual(determine_property_status(2, 0), constants.REJECTED)

    def test_end_to_end_a_property_with_no_estimate_is_flagged(self):
        profit = calculate_monthly_profit(NAN, 2_000, 2).monthly_estimated_profit
        self.assertEqual(determine_property_status(2, profit), constants.ERROR)

    def test_end_to_end_a_good_property_is_approved(self):
        profit = calculate_monthly_profit(90_000, 2_000, 2).monthly_estimated_profit
        self.assertEqual(determine_property_status(2, profit), constants.APPROVED)


class UtilitiesAndPriceTests(SimpleTestCase):
    def test_utilities_are_two_thousand_a_bedroom_a_year(self):
        self.assertEqual(calculate_utilities(0), 0)
        self.assertEqual(calculate_utilities(3), 6_000)

    def test_clean_price_strips_listing_formatting(self):
        self.assertEqual(clean_price("$2,450/mo"), 2450)
        self.assertEqual(clean_price("2450"), 2450)
        self.assertEqual(clean_price(" $1,200 "), 1200)

    def test_clean_price_returns_none_for_junk(self):
        self.assertIsNone(clean_price("Call for pricing"))
        self.assertIsNone(clean_price(None))


@override_settings(AIRDNA_URL="https://api.airdna.test/v1/bulk", AIRDNA_API_KEY="test-key")
class ProcessAirdnaApiTests(SimpleTestCase):
    """
    The retry loop used to catch requests.RequestException only, so the
    ValueError it raises itself -- and a KeyError from an unexpected response
    shape -- escaped on the first attempt without ever being retried.
    """

    def _response(self, payload=None, exc=None):
        response = mock.Mock()
        if exc is not None:
            response.raise_for_status.side_effect = exc
        response.json.return_value = payload or {}
        return response

    @mock.patch("bnbu_constants.utility.requests.post")
    def test_returns_the_results_payload(self, post):
        post.return_value = self._response({"payload": {"results": [{"a": 1}]}})
        self.assertEqual(process_airdna_api([{"address": "x"}]), [{"a": 1}])
        self.assertEqual(post.call_count, 1)

    @mock.patch("bnbu_constants.utility.requests.post")
    def test_a_request_always_carries_a_timeout(self, post):
        post.return_value = self._response({"payload": {"results": [1]}})
        process_airdna_api([{"address": "x"}])
        self.assertIn("timeout", post.call_args.kwargs)

    @mock.patch("bnbu_constants.utility.requests.post")
    def test_an_empty_result_set_is_retried_then_reported(self, post):
        post.return_value = self._response({"payload": {"results": []}})
        with self.assertRaises(ValueError):
            process_airdna_api([{"address": "x"}], retry_count=3)
        self.assertEqual(post.call_count, 3)

    @mock.patch("bnbu_constants.utility.requests.post")
    def test_an_unexpected_response_shape_is_retried(self, post):
        # A KeyError from response.json()["payload"] used to escape at once.
        post.return_value = self._response({"unexpected": True})
        with self.assertRaises(ValueError):
            process_airdna_api([{"address": "x"}], retry_count=2)
        self.assertEqual(post.call_count, 2)

    @mock.patch("bnbu_constants.utility.requests.post")
    def test_a_transient_failure_is_retried_and_then_succeeds(self, post):
        post.side_effect = [
            self._response(exc=requests.exceptions.ConnectionError("boom")),
            self._response({"payload": {"results": [{"a": 1}]}}),
        ]
        self.assertEqual(process_airdna_api([{"address": "x"}]), [{"a": 1}])
        self.assertEqual(post.call_count, 2)


class FileValidationTests(SimpleTestCase):
    def test_accepted_extensions(self):
        for name in ["listings.csv", "listings.xlsx", "listings.xls"]:
            with self.subTest(name=name):
                self.assertTrue(validate_file_type(SimpleUploadedFile(name, b"")))

    def test_rejected_extensions(self):
        for name in ["listings.pdf", "listings", "listings.csv.exe"]:
            with self.subTest(name=name):
                self.assertFalse(validate_file_type(SimpleUploadedFile(name, b"")))

    def test_missing_columns_are_reported(self):
        df = pd.DataFrame({"Location": ["1 Main St"], "Price": ["$2,000/mo"]})
        missing = validate_df(df)
        self.assertEqual(missing, {"Sq. ft.", "Ba", "Br", "Link"})

    def test_a_complete_frame_reports_nothing_missing(self):
        df = pd.DataFrame(
            {col: ["x"] for col in ["Location", "Price", "Sq. ft.", "Ba", "Br", "Link"]}
        )
        self.assertFalse(validate_df(df))

    def test_normalize_df_drops_rows_missing_the_important_columns(self):
        df = pd.DataFrame(
            {
                "Location": ["a", "b"],
                "Ba": [1.0, None],
                "Br": [2.0, 2.0],
                "Price": [2000, 2000],
                "Link": ["http://x", "http://y"],
            }
        )
        self.assertEqual(len(normalize_df(df)), 1)

    def test_normalize_column_extracts_the_leading_number(self):
        df = pd.DataFrame({"Br": ["2 bd", "3", None]})
        normalize_column(df, "Br")
        self.assertEqual(df["Br"].tolist()[:2], [2.0, 3.0])


class RentalPropertyAccessTests(APITestCase):
    """
    RentalProperty records ownership in a plain integer column, `user_id`.
    The object permission compared `obj.user`, which does not exist on the
    model, so every non-staff detail request raised AttributeError.
    """

    def setUp(self):
        self.owner = CustomUser.objects.create_user(
            email="rental-owner@example.com", password="OwnerPass!123", user_type="client"
        )
        self.intruder = CustomUser.objects.create_user(
            email="rental-intruder@example.com", password="Pass!12345", user_type="client"
        )
        self.admin = CustomUser.objects.create_superuser(
            email="rental-admin@example.com", password="AdminPass!123"
        )
        self.property = make_property(self.owner.id, location="1 Main St", batch_id=1)

    def test_owner_can_read_their_own_property(self):
        self.client.force_authenticate(user=self.owner)
        url = reverse("rental-properties-detail", kwargs={"pk": self.property.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["location"], "1 Main St")

    def test_another_user_is_refused(self):
        # 404 rather than 403: the row is outside the caller's queryset, so the
        # response does not confirm that it exists.
        self.client.force_authenticate(user=self.intruder)
        url = reverse("rental-properties-detail", kwargs={"pk": self.property.pk})
        self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)

    def test_another_user_cannot_edit_it(self):
        self.client.force_authenticate(user=self.intruder)
        url = reverse("rental-properties-detail", kwargs={"pk": self.property.pk})
        response = self.client.patch(url, {"location": "hijacked"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.property.refresh_from_db()
        self.assertEqual(self.property.location, "1 Main St")

    def test_staff_can_read_any_property(self):
        self.client.force_authenticate(user=self.admin)
        url = reverse("rental-properties-detail", kwargs={"pk": self.property.pk})
        self.assertEqual(self.client.get(url).status_code, status.HTTP_200_OK)

    def test_anonymous_access_is_refused(self):
        self.assertIn(
            self.client.get(reverse("rental-properties-list")).status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_the_list_route_does_not_expose_other_users_rows(self):
        # IsAdminOrOwnData is object-level only, so it never ran on the list
        # route; every authenticated caller saw the whole table.
        make_property(self.intruder.id, location="2 Main St", batch_id=2)
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(reverse("rental-properties-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([r["location"] for r in response.data["results"]], ["1 Main St"])

    def test_all_properties_is_scoped_too(self):
        make_property(self.intruder.id, location="2 Main St", batch_id=2)
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(reverse("rental-properties-all-properties"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([r["location"] for r in response.data["results"]], ["1 Main St"])

    def test_staff_see_every_row(self):
        make_property(self.intruder.id, location="2 Main St", batch_id=2)
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(reverse("rental-properties-list"))
        self.assertEqual(response.data["count"], 2)


class UploadPropertiesTests(APITestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_superuser(
            email="upload@example.com", password="UploadPass!123"
        )
        self.client.force_authenticate(user=self.user)
        self.url = reverse("rental-properties-upload-rental-properties")

    def test_no_file_is_a_bad_request(self):
        response = self.client.post(self.url, {}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])

    def test_wrong_extension_is_a_bad_request(self):
        upload = SimpleUploadedFile("listings.pdf", b"nonsense")
        response = self.client.post(self.url, {"file": upload}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unparseable_spreadsheet_is_a_bad_request_not_a_crash(self):
        upload = SimpleUploadedFile("listings.xlsx", b"this is not an excel file")
        response = self.client.post(self.url, {"file": upload}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_columns_are_named_in_the_error(self):
        upload = SimpleUploadedFile("listings.csv", b"Location,Price\n1 Main St,$2000\n")
        response = self.client.post(self.url, {"file": upload}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Missing columns", response.data["message"])

    @mock.patch("rental.services.process_rental_properties_task.delay")
    def test_a_valid_upload_is_queued_under_a_fresh_batch_id(self, delay):
        delay.return_value = mock.Mock(id="task-1")
        make_property(self.user.id, batch_id=7)

        csv_bytes = (
            b"Location,Price,Sq. ft.,Ba,Br,Link\n"
            b'1 Main St,"$2,000/mo",900,1,2,http://example.com/1\n'
        )
        upload = SimpleUploadedFile("listings.csv", csv_bytes)
        response = self.client.post(self.url, {"file": upload}, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data["task_id"], "task-1")
        # batch ids continue from the highest already stored.
        self.assertEqual(delay.call_args.args[1]["new_batch_id"], 8)
        self.assertEqual(delay.call_args.args[1]["user"], self.user.id)


class FilteredListTests(APITestCase):
    def setUp(self):
        self.admin = CustomUser.objects.create_superuser(
            email="filter-admin@example.com", password="AdminPass!123"
        )
        self.member = CustomUser.objects.create_user(
            email="filter-member@example.com", password="Pass!12345", user_type="client"
        )
        make_property(
            self.admin.id,
            batch_id=1,
            property_status=constants.APPROVED,
            monthly_estimated_profit=2500,
            location="1 Main St",
        )
        make_property(
            self.member.id,
            batch_id=2,
            property_status=constants.REJECTED,
            monthly_estimated_profit=100,
            location="2 Main St",
        )
        make_property(
            self.member.id,
            batch_id=3,
            property_status=constants.ERROR,
            monthly_estimated_profit=None,
            location="3 Main St",
        )
        self.url = reverse("rental-properties-filtered-list")

    def test_an_invalid_start_date_is_a_bad_request(self):
        # `status` was also the name of the local holding the status filter,
        # which shadowed the DRF status module -- this branch raised
        # AttributeError and came back as a 500.
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(self.url, {"start_date": "2024-12-03"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_an_invalid_end_date_is_a_bad_request(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(self.url, {"end_date": "03/12/2024"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_filtering_by_status_still_works(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(self.url, {"status": constants.REJECTED}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = response.data["results"]["properties"]
        self.assertEqual([r["location"] for r in rows], ["2 Main St"])

    def test_non_staff_only_see_their_own_rows(self):
        self.client.force_authenticate(user=self.member)
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        locations = {r["location"] for r in response.data["results"]["properties"]}
        self.assertEqual(locations, {"2 Main St", "3 Main St"})

    def test_batch_ids_are_reported_for_the_visible_rows(self):
        self.client.force_authenticate(user=self.member)
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(sorted(response.data["results"]["all_batch_ids"]), [2, 3])

    def test_profit_bounds_are_applied(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(self.url, {"min_profit": 1000}, format="json")
        rows = response.data["results"]["properties"]
        self.assertEqual([r["location"] for r in rows], ["1 Main St"])


class DownloadCsvTests(APITestCase):
    def setUp(self):
        self.admin = CustomUser.objects.create_superuser(
            email="csv-admin@example.com", password="AdminPass!123"
        )
        self.url = reverse("rental-properties-download-csv")

    def _body(self, response):
        return b"".join(response.streaming_content).decode()

    def test_an_invalid_date_is_a_bad_request(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self.url, {"start_date": "2024-12-03"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rows_are_streamed_with_a_header(self):
        make_property(self.admin.id, batch_id=1, location="1 Main St")
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self.url)
        body = self._body(response)
        self.assertTrue(body.startswith("Date,Batch Id,Location"))
        self.assertIn("1 Main St", body)

    def test_a_location_containing_a_comma_is_quoted(self):
        make_property(self.admin.id, batch_id=1, location="1 Main St, Apt 4")
        self.client.force_authenticate(user=self.admin)
        self.assertIn('"1 Main St, Apt 4"', self._body(self.client.get(self.url)))

    def test_a_null_location_does_not_abort_the_download(self):
        # `',' in None` is a TypeError, raised mid-stream after the headers
        # had already been sent.
        make_property(self.admin.id, batch_id=1, location=None)
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self.url)
        self.assertIn("Batch Id", self._body(response))


class PaginationTests(SimpleTestCase):
    def _page_size(self, raw):
        factory = APIRequestFactory()
        request = Request(factory.get("/", {"page_size": raw} if raw is not None else {}))
        return DefaultPagination().get_page_size(request)

    def test_default_when_unset(self):
        self.assertEqual(self._page_size(None), 10)

    def test_capped_at_the_maximum(self):
        self.assertEqual(self._page_size("1000"), 100)

    def test_non_numeric_falls_back_to_the_default(self):
        self.assertEqual(self._page_size("many"), 10)

    def test_zero_and_negative_fall_back_to_the_default(self):
        # min(0, 100) is 0 and min(-5, 100) is -5; both reached the paginator.
        self.assertEqual(self._page_size("0"), 10)
        self.assertEqual(self._page_size("-5"), 10)

    def test_a_sensible_value_is_honoured(self):
        self.assertEqual(self._page_size("25"), 25)
