"""
Tests for the shared seams.

These are the pieces every app leans on: the provider registry that decides
which language model answers, the JSON salvage used on legacy rows, the
ownership scoping that keeps one account's data out of another's, and the
readiness endpoint the container healthcheck calls.
"""

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from bnbu_core.json_utils import as_mapping, first_json_object
from bnbu_core.llm import (
    LLMUnavailable,
    Message,
    Purpose,
    available_providers,
    get_llm_provider,
    register_provider,
    reset_provider_cache,
)
from bnbu_core.llm.demo_provider import DemoProvider
from bnbu_core.llm.null_provider import NullProvider
from bnbu_core.llm.openai_provider import OpenAIProvider
from bnbu_core.pagination import DefaultPagination
from bnbu_core.testing import ScriptedProvider, use_provider
from lease.models import Document, Lease
from regulations.models import Regulations
from rental.models import RentalProperty

CustomUser = get_user_model()


class AsMappingTests(SimpleTestCase):
    def test_a_dict_passes_through(self):
        self.assertEqual(as_mapping({"a": 1}), {"a": 1})

    def test_a_json_string_is_decoded(self):
        self.assertEqual(as_mapping('{"a": 1}'), {"a": 1})

    def test_anything_else_gives_an_empty_mapping(self):
        for value in [None, "", "not json", "[1, 2]", 7, object()]:
            with self.subTest(value=value):
                self.assertEqual(as_mapping(value), {})


class FirstJsonObjectTests(SimpleTestCase):
    """Models wrap JSON in prose and fences however firmly you ask them not to."""

    def test_a_bare_object_is_found(self):
        self.assertEqual(first_json_object('{"a": 1}'), {"a": 1})

    def test_an_object_inside_prose_is_found(self):
        self.assertEqual(first_json_object('Sure!\n```json\n{"a": 1}\n```\n'), {"a": 1})

    def test_nested_braces_do_not_end_the_object_early(self):
        self.assertEqual(first_json_object('text {"a": {"b": 2}} tail'), {"a": {"b": 2}})

    def test_a_brace_inside_a_string_is_not_a_delimiter(self):
        self.assertEqual(first_json_object('{"a": "}"}'), {"a": "}"})

    def test_an_escaped_quote_does_not_end_the_string(self):
        self.assertEqual(first_json_object(r'{"a": "say \"hi\""}'), {"a": 'say "hi"'})

    def test_a_false_start_does_not_stop_the_search(self):
        self.assertEqual(first_json_object('{not json} then {"a": 1}'), {"a": 1})

    def test_nothing_parseable_gives_an_empty_mapping(self):
        for value in ["", "no braces", "{unclosed", None, 7]:
            with self.subTest(value=value):
                self.assertEqual(first_json_object(value), {})


class ProviderRegistryTests(SimpleTestCase):
    def tearDown(self):
        reset_provider_cache()

    def test_the_builtins_are_registered(self):
        self.assertTrue({"demo", "null", "openai"}.issubset(available_providers()))

    @override_settings(LLM_PROVIDER="demo")
    def test_the_setting_selects_the_provider(self):
        self.assertEqual(get_llm_provider().name, "demo")

    @override_settings(LLM_PROVIDER="", OPENAI_API_KEY="sk-test")
    def test_a_key_with_no_setting_means_openai(self):
        self.assertEqual(get_llm_provider().name, "openai")

    @override_settings(LLM_PROVIDER="", OPENAI_API_KEY=None)
    def test_no_key_and_no_setting_means_the_null_provider(self):
        self.assertEqual(get_llm_provider().name, "null")

    @override_settings(LLM_PROVIDER="does-not-exist")
    def test_an_unknown_name_degrades_to_null_rather_than_crashing(self):
        self.assertEqual(get_llm_provider().name, "null")

    @override_settings(LLM_PROVIDER="demo")
    def test_the_instance_is_reused(self):
        self.assertIs(get_llm_provider(), get_llm_provider())

    def test_a_provider_cannot_be_registered_twice_by_accident(self):
        register_provider("registry-test", NullProvider)
        with self.assertRaises(ValueError):
            register_provider("registry-test", NullProvider)
        register_provider("registry-test", NullProvider, replace=True)

    def test_an_unnamed_provider_is_refused(self):
        with self.assertRaises(ValueError):
            register_provider("  ", NullProvider)


class NullProviderTests(SimpleTestCase):
    def test_it_reports_itself_unavailable(self):
        self.assertFalse(NullProvider().is_available())

    def test_calling_it_says_what_to_configure(self):
        with self.assertRaises(LLMUnavailable) as caught:
            NullProvider().complete([Message("user", "hi")])
        self.assertIn("OPENAI_API_KEY", str(caught.exception))


class OpenAIProviderTests(SimpleTestCase):
    def test_no_key_means_unavailable(self):
        self.assertFalse(OpenAIProvider(api_key=None).is_available())

    def test_calling_without_a_key_raises_rather_than_reaching_the_network(self):
        with self.assertRaises(LLMUnavailable):
            OpenAIProvider(api_key=None).complete([Message("user", "hi")])

    def test_a_purpose_can_route_to_a_different_model(self):
        provider = OpenAIProvider(
            api_key="sk-test", model="gpt-4", model_overrides={"lease_chat": "gpt-4o-mini"}
        )
        self.assertEqual(provider.model_overrides["lease_chat"], "gpt-4o-mini")


class DemoProviderTests(SimpleTestCase):
    def test_it_is_always_available(self):
        self.assertTrue(DemoProvider().is_available())

    def test_the_lease_answer_is_the_json_the_parser_expects(self):
        completion = DemoProvider().complete([Message("user", "x")], purpose=Purpose.LEASE_ANALYSIS)
        self.assertIn('"clauses"', completion.text)

    def test_it_records_what_it_was_asked(self):
        provider = DemoProvider()
        provider.complete([Message("user", "x")], purpose=Purpose.LEASE_CHAT)
        self.assertEqual(provider.calls[0][0], Purpose.LEASE_CHAT)

    def test_an_unknown_purpose_still_answers(self):
        completion = DemoProvider().complete([Message("user", "x")], purpose="nonsense")
        self.assertIn("nonsense", completion.text)

    def test_plain_dicts_are_accepted_as_messages(self):
        completion = DemoProvider().complete([{"role": "user", "content": "x"}])
        self.assertTrue(completion.text)


class UseProviderTests(SimpleTestCase):
    def test_the_scripted_provider_is_installed_and_then_removed(self):
        scripted = ScriptedProvider("answer")
        with use_provider(scripted):
            self.assertIs(get_llm_provider(), scripted)
        self.assertIsNot(get_llm_provider(), scripted)


class PaginationTests(SimpleTestCase):
    def test_the_ceiling_is_enforced(self):
        self.assertEqual(DefaultPagination.max_page_size, 100)

    def test_the_client_can_choose_a_page_size(self):
        self.assertEqual(DefaultPagination.page_size_query_param, "page_size")


class HealthEndpointTests(APITestCase):
    def test_health_needs_no_authentication(self):
        response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "ok")

    def test_readiness_reports_the_dependencies(self):
        response = self.client.get(reverse("ready"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["checks"]["database"])
        self.assertTrue(response.data["checks"]["cache"])

    @override_settings(LLM_PROVIDER="null")
    def test_readiness_is_still_ready_with_no_language_model(self):
        reset_provider_cache()
        response = self.client.get(reverse("ready"))
        reset_provider_cache()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["integrations"]["llm_available"])


class ApiDocumentationTests(APITestCase):
    def test_the_openapi_schema_is_served(self):
        response = self.client.get(reverse("openapi-schema"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("paths", response.data)

    def test_every_app_appears_in_the_schema(self):
        paths = self.client.get(reverse("openapi-schema")).data["paths"]
        for prefix in ["/api/leases/", "/api/documents/", "/api/regulations/"]:
            self.assertIn(prefix, paths)

    def test_the_customer_detail_route_is_documented(self):
        # CustomerDetailView overrides get_object() and had no queryset, so
        # drf-yasg raised while introspecting it and the route was silently
        # dropped from the schema.
        paths = self.client.get(reverse("openapi-schema")).data["paths"]
        customer_detail = [
            path for path in paths if path.startswith("/account/clients/") and "customers" in path
        ]
        self.assertTrue(customer_detail)
        self.assertTrue(
            any("{id}" in path for path in customer_detail),
            f"no customer detail route in {customer_detail}",
        )


class SeedDemoCommandTests(TestCase):
    """The Docker stack runs this on boot, so an empty first screen is a bug."""

    def _seed(self):
        call_command("seed_demo", stdout=StringIO())

    def test_it_populates_every_app(self):
        self._seed()
        self.assertTrue(CustomUser.objects.filter(email="admin@bnbu.test").exists())
        self.assertEqual(RentalProperty.objects.count(), 8)
        self.assertEqual(Lease.objects.count(), 3)
        self.assertEqual(Regulations.objects.count(), 4)

    def test_the_second_client_owns_nothing(self):
        # rival@bnbu.test exists so the ownership scoping can be demonstrated
        # against a real account rather than only asserted in a test.
        self._seed()
        rival = CustomUser.objects.get(email="rival@bnbu.test")
        self.assertFalse(rival.is_staff)
        self.assertEqual(RentalProperty.objects.filter(user_id=rival.id).count(), 0)
        self.assertEqual(Lease.objects.filter(user=rival).count(), 0)
        self.assertEqual(Regulations.objects.filter(user=rival).count(), 0)

    def test_the_awkward_properties_are_seeded_too(self):
        # Two addresses have no revenue estimate. They must show as Error with
        # a null profit, not as a $0 rejection.
        self._seed()
        unevaluable = RentalProperty.objects.filter(property_status="Error")
        self.assertEqual(unevaluable.count(), 2)
        self.assertTrue(all(p.monthly_estimated_profit is None for p in unevaluable))

    def test_the_seeded_lease_carries_a_structured_review(self):
        self._seed()
        document = Document.objects.exclude(analysis__isnull=True).first()
        self.assertTrue(document.analysis["clauses"])
        self.assertTrue(document.analysis["structured"])

    def test_the_seeded_regulations_were_answered_by_the_curated_table(self):
        self._seed()
        for regulation in Regulations.objects.all():
            self.assertEqual(regulation.analysis_state, "complete")
            self.assertTrue(regulation.source.startswith("curated:"))

    def test_running_it_twice_changes_nothing(self):
        self._seed()
        self._seed()
        self.assertEqual(RentalProperty.objects.count(), 8)
        self.assertEqual(CustomUser.objects.filter(email="admin@bnbu.test").count(), 1)
