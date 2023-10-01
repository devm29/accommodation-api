"""
Tests for the lease app.

Two things are worth pinning down here. The first is document versioning and
the way a document's status is mirrored onto its lease -- that is the model
layer's only real behaviour. The second is access scope: a Document is reached
through its Lease, and the Lease belongs to a user, but every action on
DocumentViewSet used to look documents up by bare id against the whole table.
"""

import json
from datetime import datetime
from datetime import timezone as dt_timezone
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from bnbu_core.json_utils import as_mapping as _as_mapping
from bnbu_core.llm import Completion, LLMError
from bnbu_core.testing import ScriptedProvider, use_provider
from lease.analysis import (
    LeaseAnalysisError,
    extract_text_chunks,
    parse_completion,
    verdict_from_prose,
)
from lease.models import Document, Lease
from lease.tasks import analyze_document_task

CustomUser = get_user_model()


def make_lease(user, address1="1 Main St", **kwargs):
    return Lease.objects.create(
        user=user,
        address1=address1,
        city=kwargs.pop("city", "Seattle"),
        state=kwargs.pop("state", "WA"),
        zip_code=kwargs.pop("zip_code", "98101"),
        **kwargs,
    )


class GptResponsePayloadTests(SimpleTestCase):
    """
    `Document.gpt_response` is a JSONField, but the analysis path used to write
    into it with json.dumps(), so rows can hold a JSON *string*. Every reader
    called .get() on it, which is an AttributeError on a str.
    """

    def test_dict_passes_through(self):
        self.assertEqual(_as_mapping({"message": "hi"}), {"message": "hi"})

    def test_json_string_is_decoded(self):
        self.assertEqual(_as_mapping('{"message": "hi"}'), {"message": "hi"})

    def test_none_and_junk_give_an_empty_mapping(self):
        for value in [None, "", "not json", "[1, 2]", 7]:
            with self.subTest(value=value):
                self.assertEqual(_as_mapping(value), {})


class DocumentVersioningTests(APITestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="owner@example.com", password="OwnerPass!123", user_type="client"
        )
        self.lease = make_lease(self.user)

    def test_versions_increment_per_lease(self):
        first = Document.objects.create(lease=self.lease, name="lease.pdf")
        second = Document.objects.create(lease=self.lease, name="lease-revised.pdf")
        self.assertEqual(first.version, 1)
        self.assertEqual(second.version, 2)

    def test_versions_are_scoped_to_their_own_lease(self):
        other_lease = make_lease(self.user, address1="2 Main St")
        Document.objects.create(lease=self.lease, name="a.pdf")
        Document.objects.create(lease=self.lease, name="b.pdf")
        first_for_other = Document.objects.create(lease=other_lease, name="c.pdf")
        self.assertEqual(first_for_other.version, 1)

    def test_lease_status_follows_the_newest_document(self):
        Document.objects.create(lease=self.lease, name="a.pdf", status="Pending")
        Document.objects.create(lease=self.lease, name="b.pdf", status="Approved")
        self.lease.refresh_from_db()
        self.assertEqual(self.lease.status, "Approved")

    def test_a_document_awaiting_review_leaves_the_lease_verdict_alone(self):
        Document.objects.create(lease=self.lease, name="a.pdf", status="Approved")
        Document.objects.create(lease=self.lease, name="b.pdf")
        self.lease.refresh_from_db()
        self.assertEqual(self.lease.status, Lease.STATUS_APPROVED)

    def test_a_new_lease_with_a_pending_document_stays_a_draft(self):
        Document.objects.create(lease=self.lease, name="a.pdf")
        self.lease.refresh_from_db()
        self.assertEqual(self.lease.status, Lease.STATUS_DRAFT)

    def test_num_of_docs_counts_documents(self):
        self.assertEqual(self.lease.num_of_docs(), 0)
        Document.objects.create(lease=self.lease, name="a.pdf")
        self.assertEqual(self.lease.num_of_docs(), 1)

    def test_address_joins_the_two_address_lines(self):
        lease = make_lease(self.user, address1="1 Main St", address2="Apt 4")
        self.assertEqual(lease.address, "1 Main St, Apt 4")
        self.assertEqual(self.lease.address, "1 Main St")


class DocumentAccessScopeTests(APITestCase):
    """A document reached by id must belong to the caller (or a staff user)."""

    def setUp(self):
        self.owner = CustomUser.objects.create_user(
            email="owner2@example.com", password="OwnerPass!123", user_type="client"
        )
        self.intruder = CustomUser.objects.create_user(
            email="intruder@example.com", password="IntruderPass!123", user_type="client"
        )
        self.admin = CustomUser.objects.create_superuser(
            email="admin-lease@example.com", password="AdminPass!123"
        )
        self.lease = make_lease(self.owner)
        self.document = Document.objects.create(
            lease=self.lease,
            name="lease.pdf",
            file_url="https://example.com/lease.pdf",
            gpt_response={"message": "summary", "status": "Approved", "created_time": "t"},
        )

    def test_the_review_fields_cannot_be_written_by_the_client(self):
        self.client.force_authenticate(user=self.owner)
        url = reverse("document-detail", kwargs={"pk": self.document.pk})
        response = self.client.patch(
            url, {"status": "Approved", "analysis": {"verdict": "Approved"}}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.document.refresh_from_db()
        self.lease.refresh_from_db()
        self.assertEqual(self.document.status, Document.STATUS_PENDING)
        self.assertIsNone(self.document.analysis)
        self.assertNotEqual(self.lease.status, Lease.STATUS_APPROVED)

    def test_the_bare_document_create_route_points_at_the_upload_routes(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(reverse("document-list"), {"name": "x.pdf"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertIn("/api/leases/upload/", str(response.data))

    def test_a_lease_created_through_the_list_route_belongs_to_the_caller(self):
        # This was a 500: the serializer has no owner field and nothing set it.
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            reverse("lease-list"),
            {"address1": "9 Elm St", "city": "Austin", "state": "TX", "zip_code": "78701"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Lease.objects.get(pk=response.data["id"]).user, self.owner)

    def test_owner_can_preview_their_document(self):
        self.client.force_authenticate(user=self.owner)
        url = reverse("document-preview-document", kwargs={"document_id": self.document.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["file_url"], "https://example.com/lease.pdf")

    def test_another_user_cannot_preview_it(self):
        self.client.force_authenticate(user=self.intruder)
        url = reverse("document-preview-document", kwargs={"document_id": self.document.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_staff_can_preview_any_document(self):
        self.client.force_authenticate(user=self.admin)
        url = reverse("document-preview-document", kwargs={"document_id": self.document.pk})
        self.assertEqual(self.client.get(url).status_code, status.HTTP_200_OK)

    def test_list_only_returns_the_callers_documents(self):
        other_lease = make_lease(self.intruder, address1="9 Other Rd")
        Document.objects.create(lease=other_lease, name="theirs.pdf")

        self.client.force_authenticate(user=self.owner)
        response = self.client.get(reverse("document-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        names = [row["name"] for row in response.data["results"]]
        self.assertEqual(names, ["lease.pdf"])

    def test_chat_history_is_not_readable_by_another_user(self):
        self.client.force_authenticate(user=self.intruder)
        url = reverse("document-get-chat-history", kwargs={"pk": self.document.pk})
        self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)

    def test_owner_reads_their_own_chat_history(self):
        self.client.force_authenticate(user=self.owner)
        url = reverse("document-get-chat-history", kwargs={"pk": self.document.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["gpt_response"]["message"], "summary")
        self.assertEqual(response.data["chat_history"], [])

    def test_chat_history_survives_a_legacy_json_string_response(self):
        # Rows written before the JSONField double-encoding fix hold a string.
        self.document.gpt_response = '{"message": "legacy", "status": "Draft"}'
        self.document.save()

        self.client.force_authenticate(user=self.owner)
        url = reverse("document-get-chat-history", kwargs={"pk": self.document.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["gpt_response"]["message"], "legacy")

    def test_review_rejects_a_document_belonging_to_someone_else(self):
        self.client.force_authenticate(user=self.intruder)
        response = self.client.post(
            reverse("document-review-documents"),
            {"document_ids": [self.document.pk]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_review_rejects_ids_that_are_not_a_list_of_integers(self):
        self.client.force_authenticate(user=self.owner)
        for body in ({"document_ids": "12"}, {"document_ids": ["abc"]}, {"document_ids": 5}):
            with self.subTest(body=body):
                response = self.client.post(
                    reverse("document-review-documents"), body, format="json"
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_review_requires_document_ids(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(reverse("document-review-documents"), {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("lease.tasks.analyze_document_task.delay")
    def test_review_queues_analysis_for_the_owner(self, delay):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            reverse("document-review-documents"),
            {"document_ids": [self.document.pk]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        delay.assert_called_once_with(self.document.pk)


class LeaseScopeTests(APITestCase):
    def setUp(self):
        self.owner = CustomUser.objects.create_user(
            email="lease-owner@example.com", password="OwnerPass!123", user_type="client"
        )
        self.intruder = CustomUser.objects.create_user(
            email="lease-intruder@example.com", password="Pass!12345", user_type="client"
        )
        self.own_lease = make_lease(self.owner, address1="100 Pine St")
        self.other_lease = make_lease(self.intruder, address1="200 Pine St")

    def test_search_does_not_return_other_users_leases(self):
        # search() ran against Lease.objects.all(), ignoring get_queryset().
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(reverse("lease-search"), {"address": "Pine"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        addresses = [row["address1"] for row in response.data["results"]]
        self.assertEqual(addresses, ["100 Pine St"])

    def test_search_end_date_covers_that_whole_day_and_no_more(self):
        late = make_lease(self.owner, address1="300 Pine St")
        next_midnight = make_lease(self.owner, address1="400 Pine St")
        Lease.objects.filter(pk=late.pk).update(
            created_at=datetime(2026, 3, 16, 23, 59, tzinfo=dt_timezone.utc)
        )
        Lease.objects.filter(pk=next_midnight.pk).update(
            created_at=datetime(2026, 3, 17, 0, 0, tzinfo=dt_timezone.utc)
        )
        Lease.objects.filter(pk=self.own_lease.pk).update(
            created_at=datetime(2026, 3, 15, 12, 0, tzinfo=dt_timezone.utc)
        )
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(
            reverse("lease-search"), {"start_date": "2026-03-16", "end_date": "2026-03-16"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["address1"] for row in response.data["results"]], ["300 Pine St"])

    def test_search_rejects_a_malformed_start_date(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(reverse("lease-search"), {"start_date": "03-16-2026"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_list_is_scoped_to_the_caller(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(reverse("lease-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)

    def test_detail_of_another_users_lease_is_not_reachable(self):
        self.client.force_authenticate(user=self.owner)
        url = reverse("lease-detail", kwargs={"pk": self.other_lease.pk})
        self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)

    def test_anonymous_access_is_refused(self):
        self.assertIn(
            self.client.get(reverse("lease-list")).status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )


class AnalyzeDocumentTaskTests(APITestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="task-user@example.com", password="TaskPass!123", user_type="client"
        )
        self.lease = make_lease(self.user)
        self.document = Document.objects.create(
            lease=self.lease, name="lease.pdf", status="Pending"
        )

    @patch("lease.services.analyse_document")
    def test_a_verdict_is_written_to_the_document(self, analyse):
        analyse.return_value = _analysis(verdict="Approved", summary="looks fine")
        analyze_document_task(self.document.pk)

        self.document.refresh_from_db()
        self.assertEqual(self.document.status, "Approved")
        self.assertEqual(self.document.gpt_response["message"], "looks fine")
        self.assertEqual(self.document.analysis["verdict"], "Approved")

    @patch("lease.services.analyse_document", side_effect=LeaseAnalysisError("rate limited"))
    def test_a_failed_analysis_does_not_overwrite_the_status(self, analyse):
        # "Error" is not one of Document.STATUS_CHOICES, and Document.save()
        # mirrors the newest document's status onto the lease -- so a transient
        # OpenAI failure used to mark the whole lease "Error".
        analyze_document_task(self.document.pk)

        self.document.refresh_from_db()
        self.lease.refresh_from_db()
        self.assertEqual(self.document.status, "Pending")
        self.assertNotEqual(self.lease.status, "Error")

    @patch("lease.services.analyse_document", side_effect=RuntimeError("boom"))
    def test_an_exception_is_contained(self, analyse):
        analyze_document_task(self.document.pk)  # must not raise
        self.document.refresh_from_db()
        self.assertEqual(self.document.status, "Pending")

    def test_a_missing_document_is_not_an_error(self):
        analyze_document_task(999_999)  # must not raise


class StructuredAnalysisTests(SimpleTestCase):
    """
    The review is parsed into a typed result. The verdict used to be decided
    by ``"approved" in text.lower()``.
    """

    def _parse(self, text):
        return parse_completion(Completion(text=text, model="m", provider="p"), pages=3)

    def test_a_json_answer_becomes_a_structured_result(self):
        analysis = self._parse("""
            Here you go:
            {"verdict": "Rejected", "confidence": 0.9, "summary": "No subletting.",
             "financials": {"monthly_rent": "$2,450", "security_deposit": 4900},
             "clauses": [{"type": "subletting", "excerpt": "no subletting",
                          "risk": "high", "finding": "blocks the strategy",
                          "confidence": 0.95}]}
            """)
        self.assertTrue(analysis.structured)
        self.assertEqual(analysis.verdict, "Rejected")
        self.assertEqual(analysis.confidence, 0.9)
        self.assertEqual(analysis.financials["monthly_rent"], 2450.0)
        self.assertEqual(analysis.clauses[0].risk, "high")
        self.assertEqual(analysis.pages, 3)

    def test_an_unknown_verdict_falls_back_to_draft(self):
        self.assertEqual(self._parse('{"verdict": "Maybe"}').verdict, "Draft")

    def test_an_out_of_range_confidence_is_clamped(self):
        self.assertEqual(self._parse('{"verdict": "Draft", "confidence": 7}').confidence, 1.0)

    def test_an_unparseable_financial_value_becomes_null(self):
        analysis = self._parse('{"verdict": "Draft", "financials": {"late_fee": "lots"}}')
        self.assertIsNone(analysis.financials["late_fee"])

    def test_a_clause_with_neither_excerpt_nor_finding_is_dropped(self):
        analysis = self._parse('{"verdict": "Draft", "clauses": [{"type": "other"}]}')
        self.assertEqual(analysis.clauses, ())

    def test_an_unrecognised_risk_level_becomes_medium(self):
        analysis = self._parse(
            '{"verdict": "Draft", "clauses": [{"excerpt": "x", "finding": "y", "risk": "spicy"}]}'
        )
        self.assertEqual(analysis.clauses[0].risk, "medium")

    def test_prose_is_read_as_a_low_confidence_fallback(self):
        analysis = self._parse("This lease would be approved without changes.")
        self.assertFalse(analysis.structured)
        self.assertEqual(analysis.verdict, "Approved")
        self.assertLess(analysis.confidence, 0.5)

    def test_a_negated_approval_is_not_an_approval(self):
        # The original implementation was a bare substring test, so
        # "would not be approved" came back as Approved.
        self.assertEqual(
            verdict_from_prose("This lease would not be approved as drafted."), "Rejected"
        )

    def test_an_explicit_rejection_is_read_as_one(self):
        self.assertEqual(verdict_from_prose("Rejected: the fees are punitive."), "Rejected")

    def test_an_opinionless_answer_is_a_draft(self):
        self.assertEqual(verdict_from_prose("It depends."), "Draft")

    def test_the_legacy_payload_is_still_produced(self):
        legacy = self._parse('{"verdict": "Approved", "summary": "fine"}').as_gpt_response()
        self.assertEqual(legacy["status"], "Approved")
        self.assertEqual(legacy["message"], "fine")
        self.assertIn("created_time", legacy)


class DocumentAnalysisEndpointTests(APITestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="analysis@example.com", password="Pass!12345", user_type="client"
        )
        self.other = CustomUser.objects.create_user(
            email="analysis-other@example.com", password="Pass!12345", user_type="client"
        )
        self.lease = make_lease(self.user)
        self.document = Document.objects.create(
            lease=self.lease,
            name="lease.pdf",
            analysis={"verdict": "Draft", "confidence": 0.7, "clauses": []},
        )

    def _url(self, pk=None):
        return reverse("document-analysis", kwargs={"pk": pk or self.document.pk})

    def test_the_owner_reads_the_structured_review(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["verdict"], "Draft")

    def test_another_user_cannot_read_it(self):
        self.client.force_authenticate(user=self.other)
        self.assertEqual(self.client.get(self._url()).status_code, status.HTTP_404_NOT_FOUND)

    def test_an_unreviewed_document_says_so(self):
        unreviewed = Document.objects.create(lease=self.lease, name="new.pdf")
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self._url(unreviewed.pk))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["status"], "Pending")


class DocumentChatTests(APITestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="chat@example.com", password="Pass!12345", user_type="client"
        )
        self.lease = make_lease(self.user)
        self.document = Document.objects.create(
            lease=self.lease,
            name="lease.pdf",
            analysis={"verdict": "Draft", "summary": "A twelve-month term.", "clauses": []},
        )
        self.client.force_authenticate(user=self.user)
        self.url = reverse("document-chat-with-gpt", kwargs={"pk": self.document.pk})

    def _post(self, provider):
        with use_provider(provider):
            return self.client.post(
                self.url,
                {"document_id": self.document.pk, "message": "Which clause matters most?"},
                format="json",
            )

    def test_both_turns_are_appended(self):
        response = self._post(ScriptedProvider("The subletting ban."))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.document.refresh_from_db()
        self.assertEqual(
            [turn["role"] for turn in self.document.chat_history], ["user", "assistant"]
        )

    def test_the_review_summary_is_put_in_front_of_the_model(self):
        provider = ScriptedProvider("ok")
        self._post(provider)
        _, messages = provider.calls[0]
        self.assertIn("A twelve-month term.", messages[0].content)

    def test_an_upstream_failure_is_reported_as_unavailable(self):
        response = self._post(ScriptedProvider(error=LLMError("down")))
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

    def test_a_failed_exchange_is_not_written_to_the_history(self):
        self._post(ScriptedProvider(error=LLMError("down")))
        self.document.refresh_from_db()
        self.assertEqual(self.document.chat_history, [])

    def test_the_body_id_is_optional_and_the_url_is_enough(self):
        with use_provider(ScriptedProvider("ok")):
            response = self.client.post(self.url, {"message": "Hello?"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_a_body_id_that_contradicts_the_url_is_refused(self):
        # The action used to read the id out of the body and ignore the URL, so
        # POST /api/documents/5/chat/ with {"document_id": 9} acted on 9.
        other = Document.objects.create(lease=self.lease, name="other.pdf")
        with use_provider(ScriptedProvider("ok")):
            response = self.client.post(
                self.url,
                {"document_id": other.pk, "message": "Hello?"},
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        other.refresh_from_db()
        self.assertEqual(other.chat_history, [])


def _analysis(*, verdict, summary):
    """A LeaseAnalysis as ``analyse_document`` would return one."""
    return parse_completion(
        Completion(
            text=json.dumps({"verdict": verdict, "confidence": 0.8, "summary": summary}),
            model="scripted-1",
            provider="scripted",
        )
    )


def text_pdf(*pages):
    """A minimal PDF with one line of text per page, built without a PDF library."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        None,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    kids = []
    for text in pages:
        stream = b"BT /F1 12 Tf 72 720 Td (" + text.encode() + b") Tj ET"
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
        content_id = len(objects)
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % content_id
        )
        kids.append(b"%d 0 R" % len(objects))
    objects[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(kids), len(kids))

    body = b"%PDF-1.4\n"
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body += b"%d 0 obj\n%s\nendobj\n" % (number, obj)
    xref = len(body)
    body += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    body += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    body += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return body


class PdfExtractionTests(SimpleTestCase):
    def _fetch(self, content):
        response = Mock(content=content)
        response.raise_for_status.return_value = None
        return patch("lease.analysis.requests.get", return_value=response)

    def test_text_is_read_five_pages_to_a_chunk(self):
        pdf = text_pdf(*[f"Clause {number}" for number in range(1, 7)])
        with self._fetch(pdf):
            chunks, pages = extract_text_chunks(Mock(file_url="https://files.test/lease.pdf"))
        self.assertEqual(pages, 6)
        self.assertEqual(len(chunks), 2)
        self.assertIn("Clause 1", chunks[0])
        self.assertIn("Clause 5", chunks[0])
        self.assertIn("Clause 6", chunks[1])

    def test_a_pdf_with_no_text_is_reported_as_such(self):
        with self._fetch(text_pdf("")):
            with self.assertRaisesMessage(LeaseAnalysisError, "No extractable text"):
                extract_text_chunks(Mock(file_url="https://files.test/scan.pdf"))

    def test_bytes_that_are_not_a_pdf_are_a_lease_analysis_error(self):
        with self._fetch(b"<html>not a pdf</html>"):
            with self.assertRaisesMessage(LeaseAnalysisError, "Could not read the PDF"):
                extract_text_chunks(Mock(file_url="https://files.test/x.pdf"))
