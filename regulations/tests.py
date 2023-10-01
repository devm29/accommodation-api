"""
Tests for the regulations app.

A "regulation" is one saved question -- "can I run a short-term let at this
address?" -- together with the answer a source gave. Three things are worth
holding still: how a free-text answer becomes one of four statuses, that one
user's saved searches are not readable by another, and that the lookup no
longer blocks the request that created it.

Nothing here reaches a network. The language model is a ``ScriptedProvider``
installed through the same registry the application uses, so these tests run
the real prompts, the real parsing and the real writes.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from bnbu_core.json_utils import as_mapping as _as_mapping
from bnbu_core.llm import LLMError
from bnbu_core.regulation_sources import (
    RegulationFinding,
    RegulationSourceChain,
    RegulationSourceError,
)
from bnbu_core.regulation_sources.llm_source import classify, extract_citations
from bnbu_core.regulation_sources.static_source import StaticRegulationSource, normalise
from bnbu_core.testing import ScriptedProvider, ScriptedSource, use_provider, use_sources
from regulations import services
from regulations.models import Regulations
from regulations.serializers import RegulationsSerializer

CustomUser = get_user_model()


class GptResponsePayloadTests(SimpleTestCase):
    def test_dict_passes_through(self):
        self.assertEqual(_as_mapping({"message": "hi"}), {"message": "hi"})

    def test_json_string_is_decoded(self):
        self.assertEqual(_as_mapping('{"message": "hi"}'), {"message": "hi"})

    def test_none_and_junk_give_an_empty_mapping(self):
        for value in [None, "", "not json", "[1]", 3]:
            with self.subTest(value=value):
                self.assertEqual(_as_mapping(value), {})


class RegulationsSerializerTests(SimpleTestCase):
    def test_search_is_required(self):
        serializer = RegulationsSerializer(data={"search": ""})
        self.assertFalse(serializer.is_valid())
        self.assertIn("search", serializer.errors)


@override_settings(REGULATION_SOURCES=["llm"])
class StatusClassificationTests(APITestCase):
    """
    The answer is free text; the status column is derived from it. Note the
    ordering: "ALLOWED WITH RESTRICTIONS" contains "ALLOWED", so the more
    specific phrase has to be tested first.
    """

    def setUp(self):
        cache.clear()
        self.user = CustomUser.objects.create_user(
            email="reg-user@example.com", password="RegPass!123", user_type="client"
        )
        self.client.force_authenticate(user=self.user)

    def _create(self, answer, search="Kirkland, WA"):
        with use_provider(ScriptedProvider(answer)):
            response = self.client.post(
                reverse("regulations-list"), {"search": search}, format="json"
            )
        # The lookup needs a network call, so the row comes back queued; the
        # eager worker has already filled it in by the time we read it.
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data["analysis_state"], "pending")
        return Regulations.objects.get(pk=response.data["id"])

    def test_plain_allowed(self):
        regulation = self._create("SHORT TERM RENTAL ALLOWED. Nothing to worry about.")
        self.assertEqual(regulation.status, "STR Allowed")

    def test_allowed_with_restrictions_wins_over_allowed(self):
        regulation = self._create(
            "SHORT TERM RENTAL ALLOWED WITH RESTRICTIONS - 245 days of owner occupancy."
        )
        self.assertEqual(regulation.status, "STR Allowed with Restrictions")

    def test_not_allowed(self):
        regulation = self._create("SHORT TERM RENTAL NOT ALLOWED in this zone.")
        self.assertEqual(regulation.status, "STR Not Allowed")

    def test_an_unclassifiable_answer_stays_pending(self):
        regulation = self._create("It depends on several factors.")
        self.assertEqual(regulation.status, "pending")

    def test_the_answer_is_stored_on_the_row(self):
        regulation = self._create("SHORT TERM RENTAL ALLOWED.")
        self.assertIn("ALLOWED", regulation.gpt_response["message"])
        self.assertIn("created_time", regulation.gpt_response)
        self.assertEqual(regulation.analysis_state, "complete")

    def test_the_source_that_answered_is_recorded(self):
        regulation = self._create("SHORT TERM RENTAL ALLOWED.")
        self.assertTrue(regulation.source.startswith("llm:"))

    def test_citations_are_pulled_out_of_the_answer(self):
        regulation = self._create(
            "SHORT TERM RENTAL ALLOWED. See [the code](https://example.gov/str)."
        )
        self.assertEqual(
            regulation.gpt_response["citations"], ["the code (https://example.gov/str)"]
        )

    def test_a_failed_lookup_does_not_write_a_bogus_status(self):
        # The failure path returns status 'error', which is not one of
        # STATUS_CHOICES; writing it left a row that no status filter matched.
        with use_provider(ScriptedProvider(error=LLMError("openai is down"))):
            response = self.client.post(
                reverse("regulations-list"), {"search": "Kirkland, WA"}, format="json"
            )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        regulation = Regulations.objects.get(pk=response.data["id"])
        self.assertEqual(regulation.status, "pending")
        self.assertEqual(regulation.gpt_response["status"], "error")
        self.assertEqual(regulation.analysis_state, "failed")

    def test_the_row_is_owned_by_the_caller(self):
        regulation = self._create("SHORT TERM RENTAL ALLOWED.")
        self.assertEqual(regulation.user, self.user)

    def test_a_blank_search_is_rejected(self):
        response = self.client.post(reverse("regulations-list"), {"search": "   "}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class ClassificationUnitTests(SimpleTestCase):
    def test_restrictions_are_read_before_a_plain_allow(self):
        self.assertEqual(
            classify("SHORT TERM RENTAL ALLOWED WITH RESTRICTIONS"),
            "STR Allowed with Restrictions",
        )

    def test_an_answer_with_no_verdict_line_is_pending(self):
        self.assertEqual(classify("Probably fine."), "pending")

    def test_markdown_links_become_citations(self):
        self.assertEqual(
            extract_citations("see [KZC 5.10](https://example.gov/kzc)"),
            ("KZC 5.10 (https://example.gov/kzc)",),
        )

    def test_no_links_means_no_citations(self):
        self.assertEqual(extract_citations("no links here"), ())


class CuratedSourceTests(SimpleTestCase):
    def setUp(self):
        self.source = StaticRegulationSource()

    def test_normalise_folds_case_and_punctuation(self):
        self.assertEqual(normalise("Kirkland, WA"), normalise("  kirkland   wa "))

    def test_a_known_city_is_answered_locally(self):
        finding = self.source.lookup("Kirkland, WA")
        self.assertEqual(finding.status, "STR Allowed with Restrictions")
        self.assertTrue(finding.citations)

    def test_a_full_address_still_resolves_to_its_city(self):
        finding = self.source.lookup("123 Main St, Kirkland WA 98033")
        self.assertIsNotNone(finding)
        self.assertIn("Kirkland", finding.source)

    def test_an_unknown_place_is_not_answered(self):
        self.assertIsNone(self.source.lookup("Ulaanbaatar, Mongolia"))

    def test_the_curated_source_answers_without_a_network_call(self):
        self.assertTrue(self.source.is_offline)


class SourceChainTests(SimpleTestCase):
    def test_the_first_source_with_an_answer_wins(self):
        first = ScriptedSource(None)
        second = ScriptedSource(
            RegulationFinding(status="STR Allowed", summary="yes", source="second")
        )
        chain = RegulationSourceChain([first, second])
        self.assertEqual(chain.lookup("anywhere").source, "second")
        self.assertEqual(first.queries, ["anywhere"])

    def test_a_broken_source_does_not_stop_the_chain(self):
        broken = ScriptedSource(error=RegulationSourceError("upstream down"))
        working = ScriptedSource(
            RegulationFinding(status="STR Allowed", summary="yes", source="working")
        )
        chain = RegulationSourceChain([broken, working])
        self.assertEqual(chain.lookup("anywhere").source, "working")

    def test_every_source_failing_is_reported(self):
        chain = RegulationSourceChain([ScriptedSource(error=RegulationSourceError("no"))])
        with self.assertRaises(RegulationSourceError):
            chain.lookup("anywhere")

    def test_no_source_knowing_is_not_an_error(self):
        chain = RegulationSourceChain([ScriptedSource(None)])
        self.assertIsNone(chain.lookup("anywhere"))

    def test_an_unavailable_source_is_skipped(self):
        skipped = ScriptedSource(
            RegulationFinding(status="STR Allowed", summary="yes", source="skipped"),
            available=False,
        )
        self.assertIsNone(RegulationSourceChain([skipped]).lookup("anywhere"))
        self.assertEqual(skipped.queries, [])


class LookupRoutingTests(APITestCase):
    """
    Where the answer comes from, and whether the caller has to wait for it.

    This is the scalability change: creating a regulation used to run a GPT-4
    call inside the POST.
    """

    def setUp(self):
        cache.clear()
        self.user = CustomUser.objects.create_user(
            email="routing@example.com", password="RegPass!123", user_type="client"
        )
        self.client.force_authenticate(user=self.user)

    @override_settings(REGULATION_SOURCES=["curated", "llm"])
    def test_a_curated_city_is_answered_inside_the_request(self):
        provider = ScriptedProvider("SHORT TERM RENTAL ALLOWED.")
        with use_provider(provider):
            response = self.client.post(
                reverse("regulations-list"), {"search": "New York, NY"}, format="json"
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["analysis_state"], "complete")
        self.assertEqual(response.data["status"], "STR Not Allowed")
        # The curated table answered, so the model was never asked.
        self.assertEqual(provider.calls, [])

    @override_settings(REGULATION_SOURCES=["llm"])
    def test_a_model_lookup_is_queued_rather_than_awaited(self):
        with use_provider(ScriptedProvider("SHORT TERM RENTAL ALLOWED.")):
            response = self.client.post(
                reverse("regulations-list"), {"search": "Boise, ID"}, format="json"
            )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data["analysis_state"], "pending")
        self.assertIsNone(response.data["gpt_response"])

    @override_settings(REGULATION_SOURCES=["llm"])
    def test_a_repeat_search_is_answered_from_the_cache(self):
        provider = ScriptedProvider(["SHORT TERM RENTAL ALLOWED.", "should not be asked twice"])
        with use_provider(provider):
            self.client.post(reverse("regulations-list"), {"search": "Boise, ID"}, format="json")
            second = self.client.post(
                reverse("regulations-list"), {"search": "boise id"}, format="json"
            )
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.data["analysis_state"], "complete")
        # One model call for two searches of the same place, spelt differently.
        self.assertEqual(len(provider.calls), 1)

    @override_settings(REGULATION_SOURCES=["llm"])
    def test_reanalyze_ignores_the_cache(self):
        with use_provider(ScriptedProvider("SHORT TERM RENTAL ALLOWED.")):
            created = self.client.post(
                reverse("regulations-list"), {"search": "Boise, ID"}, format="json"
            )
        url = reverse("regulations-reanalyze", kwargs={"pk": created.data["id"]})
        with use_provider(ScriptedProvider("SHORT TERM RENTAL NOT ALLOWED.")):
            response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "STR Not Allowed")

    def test_a_search_no_source_can_answer_is_recorded_as_failed(self):
        with use_sources("scripted", scripted=ScriptedSource(None)):
            response = self.client.post(
                reverse("regulations-list"), {"search": "Atlantis"}, format="json"
            )
        regulation = Regulations.objects.get(pk=response.data["id"])
        self.assertEqual(regulation.analysis_state, "failed")

    def test_the_cache_key_ignores_spelling_and_case(self):
        self.assertEqual(services.cache_key("Kirkland, WA"), services.cache_key("kirkland wa"))


class RegulationScopeTests(APITestCase):
    def setUp(self):
        self.owner = CustomUser.objects.create_user(
            email="reg-owner@example.com", password="OwnerPass!123", user_type="client"
        )
        self.intruder = CustomUser.objects.create_user(
            email="reg-intruder@example.com", password="Pass!12345", user_type="client"
        )
        self.admin = CustomUser.objects.create_superuser(
            email="reg-admin@example.com", password="AdminPass!123"
        )
        self.regulation = Regulations.objects.create(
            user=self.owner,
            search="Kirkland, WA",
            status="STR Allowed",
            gpt_response={"message": "summary", "status": "STR Allowed", "created_time": "t"},
        )
        Regulations.objects.create(user=self.intruder, search="Austin, TX")

    def test_list_only_shows_the_callers_rows(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(reverse("regulations-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([r["search"] for r in response.data["results"]], ["Kirkland, WA"])

    def test_search_does_not_leak_other_users_rows(self):
        # search() ran against Regulations.objects.all(), bypassing get_queryset().
        self.client.force_authenticate(user=self.intruder)
        response = self.client.get(reverse("regulations-search"), {"query": "Kirkland"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"], [])

    def test_search_rejects_a_malformed_end_date(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(reverse("regulations-search"), {"end_date": "16/03/2026"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_staff_search_sees_everything(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(reverse("regulations-search"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 2)

    def test_chat_history_of_another_users_regulation_is_not_reachable(self):
        self.client.force_authenticate(user=self.intruder)
        url = reverse("regulations-get-chat-history", kwargs={"pk": self.regulation.pk})
        self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)

    def test_owner_reads_their_own_chat_history(self):
        self.client.force_authenticate(user=self.owner)
        url = reverse("regulations-get-chat-history", kwargs={"pk": self.regulation.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["gpt_response"]["message"], "summary")

    def test_chat_cannot_target_another_users_regulation(self):
        self.client.force_authenticate(user=self.intruder)
        url = reverse("regulations-chat-with-gpt", kwargs={"pk": self.regulation.pk})
        response = self.client.post(
            url, {"message": "hello", "regulation_id": self.regulation.pk}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_chat_appends_both_turns_to_the_history(self):
        self.client.force_authenticate(user=self.owner)
        url = reverse("regulations-chat-with-gpt", kwargs={"pk": self.regulation.pk})
        with use_provider(ScriptedProvider("Here is what the ordinance says.")):
            response = self.client.post(
                url,
                {"message": "Any loopholes?", "regulation_id": self.regulation.pk},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.regulation.refresh_from_db()
        roles = [turn["role"] for turn in self.regulation.chat_history]
        self.assertEqual(roles, ["user", "assistant"])
        self.assertEqual(self.regulation.chat_history[0]["content"], "Any loopholes?")

    def test_chat_reports_an_upstream_failure_as_unavailable(self):
        # A vendor outage is the vendor's fault, not a bug in this service:
        # 503, so a client knows to retry.
        self.client.force_authenticate(user=self.owner)
        url = reverse("regulations-chat-with-gpt", kwargs={"pk": self.regulation.pk})
        with use_provider(ScriptedProvider(error=LLMError("down"))):
            response = self.client.post(
                url, {"message": "hi", "regulation_id": self.regulation.pk}, format="json"
            )
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

    def test_chat_with_no_provider_configured_is_refused_cleanly(self):
        self.client.force_authenticate(user=self.owner)
        url = reverse("regulations-chat-with-gpt", kwargs={"pk": self.regulation.pk})
        with override_settings(LLM_PROVIDER="null"):
            response = self.client.post(
                url, {"message": "hi", "regulation_id": self.regulation.pk}, format="json"
            )
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertIn("LLM_PROVIDER", response.data["error"])

    def test_chat_requires_a_message(self):
        self.client.force_authenticate(user=self.owner)
        url = reverse("regulations-chat-with-gpt", kwargs={"pk": self.regulation.pk})
        response = self.client.post(url, {"regulation_id": self.regulation.pk}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_the_body_id_is_optional_and_the_url_is_enough(self):
        self.client.force_authenticate(user=self.owner)
        url = reverse("regulations-chat-with-gpt", kwargs={"pk": self.regulation.pk})
        with use_provider(ScriptedProvider("ok")):
            response = self.client.post(url, {"message": "Any loopholes?"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_a_body_id_that_contradicts_the_url_is_refused(self):
        # The action used to trust the body and ignore the URL entirely.
        self.client.force_authenticate(user=self.owner)
        url = reverse("regulations-chat-with-gpt", kwargs={"pk": self.regulation.pk})
        with use_provider(ScriptedProvider("ok")):
            response = self.client.post(
                url,
                {"regulation_id": self.regulation.pk + 1000, "message": "hi"},
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_anonymous_access_is_refused(self):
        self.assertIn(
            self.client.get(reverse("regulations-list")).status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )
