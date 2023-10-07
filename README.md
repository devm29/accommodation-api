# BnBu API

Underwriting for short-term-rental investors, as a JSON API. Upload a spreadsheet of
listings and it asks AirDNA what each address should earn as an Airbnb, subtracts rent and
utilities, and says which are worth taking on. It also reviews lease PDFs clause by clause
through a language model, and answers "can I legally run a short-term let here?".

Django 5.1 · DRF · Celery + Redis · PostgreSQL · four domain apps · 222 tests. No product UI
lives here; the screens are a separate React repo. Every request and response below was
captured with `curl` against the Docker stack with `LLM_PROVIDER=demo` and no API keys set —
the transcript, plus shots of the [admin](docs/01-properties.png) and
[Swagger](docs/02-swagger.png), is [`docs/api-walkthrough.md`](docs/api-walkthrough.md).

## The request that used to hold a worker open

The module docstring of [`regulations/services.py`](regulations/services.py) records what
creating a regulation search used to be: a GPT-4 call inline, inside `perform_create`,
inside the POST. With three gunicorn workers, three people asking about three cities is the
whole API. `create_regulation()` now tries Redis, then any source that answers locally, then
the queue — and the seam is what makes that a property of the design rather than a special
case: a `RegulationSource` declares `is_offline` (`bnbu_core/regulation_sources/base.py`),
`_try_offline_sources()` consults only those inside a request, and everything else gets a
`202` the client polls.

```mermaid
sequenceDiagram
    actor C as Client
    participant V as RegulationsViewSet + services
    participant R as Redis cache
    participant CU as curated source (is_offline true)
    participant W as Celery worker + RegulationSourceChain
    C->>V: POST /api/regulations/ with a location
    V->>R: get(cache_key(normalise(search)))
    alt asked about recently
        R-->>V: stored answer payload
        V-->>C: 201, analysis_state complete
    else the curated table covers it
        V->>CU: lookup(search)
        CU-->>V: RegulationFinding, cached for REGULATION_CACHE_TTL
        V-->>C: 201, analysis_state complete
    else no offline source knows
        V->>W: analyze_regulation_task.delay(row id)
        V-->>C: 202, analysis_state pending, gpt_response null
        W->>W: lookup over curated then llm, cache it, write the row
        C->>V: GET /api/regulations/{id}/
        V-->>C: 200, analysis_state complete
    end
```

The curated table covers `Santa Monica, CA`, so that search is answered inside the request.
It does not cover `Porto, Portugal`, so that one is not waited on:

```console
$ curl -sX POST .../api/regulations/ -d '{"search":"santa monica ca"}' -w 'HTTP %{http_code}\n'
HTTP 201
{ "id": 5, "status": "STR Not Allowed", "analysis_state": "complete", "source": "curated:Santa Monica, CA",
  "gpt_response": { "confidence": 0.93, "message": "...",
                    "citations": ["Santa Monica Municipal Code Ch. 6.20"] } }

$ curl -sX POST .../api/regulations/ -d '{"search":"Porto, Portugal"}' -w 'HTTP %{http_code}\n'
HTTP 202
{ "id": 6, "status": "pending", "gpt_response": null, "analysis_state": "pending" }

$ docker compose logs worker | tail -1
worker-1  | Task regulations.tasks.analyze_regulation_task[00472358-...] succeeded in 0.15s
```

The cache key is the location normalised, so `Kirkland, WA` and `kirkland wa` are one
question. The routing is held by `test_a_curated_city_is_answered_inside_the_request`,
`test_a_model_lookup_is_queued_rather_than_awaited` and
`test_a_repeat_search_is_answered_from_the_cache`. A broken source is logged and skipped
rather than taking the feature down, and a municipal-code index or paid feed would be a class
with one method and a registry line.

## What a lease review returns

Not a paragraph. `lease/analysis.py` asks for a JSON object and validates it into a
`LeaseAnalysis` — verdict, confidence, the money, and one `ClauseFinding` per clause that
matters, each with the lease's own words and its own confidence, at
`GET /api/documents/<id>/analysis/`:

```console
{ "verdict": "Draft", "confidence": 0.74, "structured": true, "provider": "demo", "pages": 14,
  "financials": { "monthly_rent": 2450.0, "security_deposit": 4900.0,
                  "late_fee": 150.0, "lease_term_months": 12.0 },
  "clauses": [
    { "type": "subletting", "risk": "high", "confidence": 0.93,
      "excerpt": "Tenant shall not sublet the Premises or any portion thereof, nor assign this Lease, under any circumstance.",
      "finding": "An absolute ban with no landlord-consent carve-out. For a short-term-rental strategy this clause alone makes the unit unusable..." } ],
  "summary": "A twelve-month fixed term at $2,450 a month with a two-month deposit..." }
```

One of four clauses, abridged — the whole body is in
[`docs/api-walkthrough.md`](docs/api-walkthrough.md) §7. The parse assumes the model will not
cooperate. `first_json_object` scans for the first balanced object, respecting strings and
escapes, because models wrap JSON in prose whatever the prompt says. With nothing parseable it
falls back to reading the prose, recording a confidence of `0.3` and `"structured": false`, so
a guess is visibly a guess. Every field is validated inbound: an unknown verdict becomes
`Draft`, an unknown risk `medium`, confidences are clamped to 0..1, `"$2,450"` is coerced to a
number, a clause with neither excerpt nor finding is dropped. And the prose path is
negation-aware — the verdict used to be decided with
`if "approved" in text.lower()`, which reads *"this lease would not be approved"*
as an approval, and `test_a_negated_approval_is_not_an_approval` pins it.

A failed run writes **nothing** — the task returns a status deliberately outside
`Document.STATUS_CHOICES`, because `Document.save()` mirrors the newest document's status
onto its lease and "OpenAI had a bad minute" is not a verdict on a lease. A failed
regulation lookup likewise records `analysis_state: "failed"` and leaves `status` at
`pending`, rather than writing a value no filter matches.

## Two authorization defects, and where they stand now

**The password-reset endpoint used to return the reset token in its response body** —
account takeover from an email address alone. `accounts.services.send_password_reset` now
puts the token in the mail and nowhere else, and `POST /account/password/reset/` answers
`200` with no link in it. `test_the_reset_token_is_not_returned_in_the_response` asserts
both that `reset_link` is absent and that no `/reset/` path appears anywhere in the body.

**Row scoping lives in the queryset, not in object permissions**, because object-level
permissions never run on list routes. `bnbu_core.mixins.OwnerScopedQuerysetMixin` narrows
every queryset to the caller unless they are staff, on all four owner-scoped viewsets,
`owner_field` covering the models that spell ownership differently (`user_id` on properties,
`lease__user` on documents) — so a new action is scoped by default rather than by
remembering, and comments in [`lease/views.py`](lease/views.py) and
[`regulations/views.py`](regulations/views.py) mark the actions that used to reach for
`Model.objects.all()`. Someone else's row is a `404`, not a `403`, so the response does not
confirm the id exists — pinned by `test_the_list_route_does_not_expose_other_users_rows`,
`test_chat_cannot_target_another_users_regulation` and siblings in every app, and demonstrable
against the seeded account that owns nothing.

## The four apps

| App | What it owns |
| --- | --- |
| `accounts` | Email-based `CustomUser`, JWT and session auth, an admin → client → customer hierarchy |
| `rental` | Spreadsheet ingest, the AirDNA batch, the profit maths, filters, CSV export |
| `lease` | Leases, versioned documents on Cloudinary, the structured review, chat over it |
| `regulations` | One saved STR-legality question per row, its answer, chat over it |

A `CustomUser` owns leases and regulation searches by foreign key and may be a client with
customers beneath it (a self-FK); a lease has versioned documents and mirrors the status of
its newest. `RentalProperty` keeps its owner in a plain integer column rather than a
relation, so its index is declared explicitly and its permission class compares ids.

Each app has a `services.py` holding its use cases; a DRF action parses, calls one service
function, and renders. `bnbu_core/` holds what all four need — the language-model registry,
the regulation-source registry, owner scoping, pagination, JSON salvage for legacy rows,
health endpoints, `seed_demo` — and imports none of the four apps, so dependencies point
inward. `bnbu_constants/` holds the profit maths, the AirDNA client and the spreadsheet
column contract. A property AirDNA cannot price is `Error` with a **null** profit, never
`$0.00` and `Rejected`: `nan >= 1000` is `False`, so a verdict used to be reported on an
investment nothing was known about (`test_unknown_profit_is_an_error_not_a_rejection`).

## Queries, payloads, indexes

- **The lease list.** `num_of_docs` is a model method, so serializing a page of ten leases
  issued ten `COUNT`s on top of ten more for the nested documents. `get_base_queryset`
  annotates the count — shadowing the method — and `Prefetch`es the documents, so a page
  costs one query for the page, one for its documents, one for the pagination count. Nested
  documents use `DocumentSummarySerializer`, keeping full review text and chat transcripts
  on the document routes.
- **Seven named indexes**, all added in the three most recent migrations:
  `{lease,regulation,rental}_user_recent_idx` for "this user's rows, newest first",
  `rental_status_profit_idx` for the profit filter, `document_lease_version_idx` for version
  lookups, and a status index on leases and regulations.
- **Nothing unbounded.** Every list route paginates (`page_size` up to 100), `batch_ids()`
  collects distinct batches with `DISTINCT` in SQL, the CSV export iterates with
  `.iterator(chunk_size=500)` so a `StreamingHttpResponse` is not quietly materialising
  every row first, and `LEASE_MAX_CHUNKS` (12) caps the five-page chunks per review while
  chat replays 20 turns, not a whole history.
- **Workers lose nothing.** `task_acks_late` with `worker_prefetch_multiplier=1` hands an
  interrupted review back to the queue, under a 540-second soft limit.

## Running it

`docker compose up --build` waits for Postgres, migrates, collects static files, seeds
(`Seeded 8 properties, 3 leases and 4 regulation searches.`) and starts the API and a Celery
worker. Build and boot were both verified — `docker compose build`, then
`docker compose up -d` with all four services (`db`, `redis`, `api`, `worker`) reporting
`healthy`, then a token exchange, a paginated property list and a `202` regulation POST
against the live stack, from a multi-stage, non-root, healthchecked image.

The API answers on <http://localhost:8131> — Swagger at `/api/docs/`, ReDoc at `/api/redoc/`,
the schema at `/api/schema.json`, Django's admin at `/admin/`, probes at `/api/health/` and
`/api/ready/` — with Postgres on `localhost:8132` and Redis on `localhost:8133`.

Three seeded accounts share the password `DemoPass!123`: `admin@bnbu.test` (staff, sees
everything), `analyst@bnbu.test` (owns the seeded data), `rival@bnbu.test` (owns nothing).

```bash
TOKEN=$(curl -s -X POST http://localhost:8131/api/token/ \
  -H 'Content-Type: application/json' \
  -d '{"email":"analyst@bnbu.test","password":"DemoPass!123"}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access"])')
curl -s http://localhost:8131/api/rental_properties/ -H "Authorization: Bearer $TOKEN"
```

**No API keys are needed.** Compose sets `LLM_PROVIDER=demo`, which returns deterministic
payloads through the same parsing, validation and persistence code the live pipeline uses, and
`/api/ready/` reports which integrations are wired up while still answering `ready` with none
of them. Set `OPENAI_API_KEY` and drop `LLM_PROVIDER` to go live, and `docker compose down -v`
to tear it down.

### Configuration

Everything is read from the environment, a `.env` file is loaded automatically, and every
integration is optional — the Docker stack needs none of it set. `DATABASE_URL` (any
`dj-database-url` URL) and `REDIS_URL` (`rediss://` for TLS) point at the infrastructure,
`ENVIRONMENT=staging|production` tightens `ALLOWED_HOSTS` and makes `SECRET_KEY` mandatory,
`CORS_ALLOWED_ORIGINS` is a **JSON array**, and the model path is tuned by `LLM_PROVIDER`,
`LLM_MODEL`, `LLM_MODEL_OVERRIDES` (purpose → model, so chat can run cheap while extraction
keeps the strong one), `LEASE_MAX_CHUNKS`, `REGULATION_SOURCES` and `REGULATION_CACHE_TTL`.
`OPENAI_API_KEY`, `AIRDNA_*`, `CLOUDINARY_*` and `CLICKUP_*` are each needed only by the
feature that uses them — a missing `CLICKUP_URL` skips that push and logs it, and
`bnbu_backend_api/settings.py` documents each where it is read.

## Without Docker

```bash
pip install -r requirements.txt   # in a virtualenv
export DATABASE_URL=postgres://dev_user:dev_password@localhost:5432/dev_database
python manage.py migrate && python manage.py seed_demo   # seeding is idempotent
python manage.py runserver
celery -A bnbu_backend_api worker -l info                # second shell, needs Redis

python manage.py test             # 222 tests, database only
```

**No test can make a real OpenAI call**: the default provider under test is `null`, so a
forgotten mock fails loudly instead of billing, and tests that need a model install a
`ScriptedProvider` through the same registry the application uses — exercising real prompt
building, parsing and persistence rather than a mock's return value. The cache is in-memory
and Celery runs tasks inline. `isort`, `black` (100 columns) and `flake8` pass, and
`.github/workflows/ci.yml` adds `makemigrations --check`, so a model change without a
migration fails the build.

## What this does not do

- **The curated regulation table is six illustrative cities**, summarised from their
  ordinances so the chain has a working first source and the demo answers with no API key.
  Not legal advice, and not maintained against amendments.
- **No RAG.** Regulation answers are the model's own knowledge plus that table. A retrieval
  index over municipal codes is the next step and the reason `RegulationSource` exists; a
  vector store with no corpus behind it would be theatre.
- **Text PDFs only** — a scanned lease yields no extractable text, is reported as such, and
  gets no OCR.
- **Lease and regulation chat are still synchronous.** Interactive, so a queue would not
  help the user, but they hold a worker for a whole completion; streaming is the right fix
  and is not done. Nor is throttling — DRF throttling is unconfigured, so a busy account can
  queue as many reviews as it likes.
- **The demo provider is a fixture, not a model.** Under `LLM_PROVIDER=demo` every lease
  produces the same four clause findings — there to show the product working, not analysis.
