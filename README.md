# BnBu API

Underwriting for short-term-rental investors, as a JSON API. Upload a spreadsheet of
listings and it asks AirDNA what each address should earn as an Airbnb, subtracts rent and
utilities, and says which are worth taking on. It also reviews lease PDFs clause by clause
through a language model, and answers "can I legally run a short-term let here?".

Django 5.2 LTS · Python 3.12 · DRF · Celery + Redis · PostgreSQL · four domain apps · 250
tests. No product UI lives here; the screens are a separate React repo. Every request and
response below was captured with `curl` against the Docker stack with `LLM_PROVIDER=demo` and
no API keys set. The full transcript is [`docs/api-walkthrough.md`](docs/api-walkthrough.md).

## The request that used to hold a worker open

The module docstring of [`regulations/services.py`](regulations/services.py) records what
creating a regulation search used to be: a GPT-4 call inline, inside `perform_create`,
inside the POST. With three gunicorn workers, three people asking about three cities is the
whole API. `create_regulation()` now tries Redis, then any source that answers locally, then
the queue — and the seam is what makes that a property of the design rather than a special
case: a `RegulationSource` declares `is_offline` (`bnbu_core/regulation_sources/base.py`),
`_try_offline_sources()` consults only those inside a request, and everything else gets a
`202` the client polls. `POST /api/regulations/<id>/reanalyze/` drops the cached answer and
goes through the same routing; it used to call the model inline as well.

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
$ curl -s -X POST localhost:8131/api/regulations/ -H "Authorization: Bearer $TOKEN" \
    -H 'Content-Type: application/json' -d '{"search":"santa monica ca"}'
HTTP 201
{ "id": 5, "status": "STR Not Allowed", "analysis_state": "complete",
  "source": "curated:Santa Monica, CA",
  "gpt_response": { "confidence": 0.93, "message": "Only home-sharing is legal; ...",
                    "citations": ["Santa Monica Municipal Code Ch. 6.20"] } }

$ curl -s -X POST localhost:8131/api/regulations/ -H "Authorization: Bearer $TOKEN" \
    -H 'Content-Type: application/json' -d '{"search":"Porto, Portugal"}'
HTTP 202
{ "id": 6, "status": "pending", "gpt_response": null, "analysis_state": "pending", "source": "" }

$ docker compose logs worker | grep succeeded
worker-1  | ... Task regulations.tasks.analyze_regulation_task[82115cb8-...] succeeded in 0.02s: None

$ curl -s localhost:8131/api/regulations/6/ -H "Authorization: Bearer $TOKEN"
HTTP 200
{ "id": 6, "status": "STR Allowed with Restrictions", "analysis_state": "complete",
  "source": "llm:demo", ... }
```

The cache key is the location normalised, so `Kirkland, WA` and `kirkland wa` are one
question. The routing is held by `test_a_curated_city_is_answered_inside_the_request`,
`test_a_model_lookup_is_queued_rather_than_awaited`,
`test_a_repeat_search_is_answered_from_the_cache` and
`test_reanalyze_does_not_call_the_model_inside_the_request`. A broken source is logged and
skipped rather than taking the feature down, and a municipal-code index or paid feed would be
a class with one method and a registry line.

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

The text comes out of the PDF with `pypdf`, five pages to a chunk and at most
`LEASE_MAX_CHUNKS` chunks, so one upload cannot fan out into an unbounded number of paid calls.

A failed run writes **nothing** — the task returns a status deliberately outside
`Document.STATUS_CHOICES`, because `Document.save()` mirrors the newest document's verdict
onto its lease and "OpenAI had a bad minute" is not a verdict on a lease. For the same reason a
document waiting in the queue leaves its lease's verdict alone; it used to copy `Pending` onto
the lease, a value no lease filter matches. A failed regulation lookup records
`analysis_state: "failed"` and leaves `status` at `pending`.

Only the server writes a review. `status`, `analysis`, `gpt_response` and `chat_history` are
read-only on `/api/documents/<id>/`, so a `PATCH` cannot mark a lease approved:

```console
$ curl -s -X PATCH localhost:8131/api/documents/2/ -H "Authorization: Bearer $TOKEN" \
    -H 'Content-Type: application/json' -d '{"status":"Approved"}'
HTTP 200
{ "id": 2, "lease_id": 2, "name": "cypress-ave-lease.pdf", "status": "Draft", ... }
```

## Authorization: what was wrong and where it stands

**The password-reset endpoint used to return the reset token in its response body** —
account takeover from an email address alone. `accounts.services.send_password_reset` now
puts the token in the mail and nowhere else. `POST /account/password/reset/` answers `200`
with the same message whether or not the address has an account, so it does not reveal which
addresses are registered either.

**A client could make an admin.** The customer routes saved `user_type` and `client` from
the request body, so a client could create a customer as `"user_type": "admin"`, or promote
an existing one with `PUT` or `PATCH`. That account could then list and edit every other
client's customers. The routes now always save `user_type=customer`, only an admin may move a
customer to another client, and only clients and admins may create customers at all
(`CustomerPrivilegeTests`).

**Owners come from the request, not the body.** `RentalProperty.user_id` was writable, so a
caller could file rows under another account or hand their own away; it is read-only now and
set from the token. `POST /api/leases/` never set an owner at all and answered `500`.

**Row scoping lives in the queryset, not in object permissions**, because object-level
permissions never run on list routes. `bnbu_core.mixins.OwnerScopedQuerysetMixin` narrows
every queryset to the caller unless they are staff, on all four owner-scoped viewsets,
`owner_field` covering the models that spell ownership differently (`user_id` on properties,
`lease__user` on documents) — so a new action is scoped by default rather than by
remembering, and comments in [`lease/views.py`](lease/views.py) and
[`regulations/views.py`](regulations/views.py) mark the actions that used to reach for
`Model.objects.all()`. Someone else's row is a `404`, not a `403`, so the response does not
confirm the id exists:

```console
$ curl -s localhost:8131/api/regulations/6/
HTTP 401
{ "detail": "Authentication credentials were not provided." }

$ curl -s localhost:8131/api/regulations/6/ -H "Authorization: Bearer $RIVAL_TOKEN"
HTTP 404
{ "detail": "No Regulations matches the given query." }
```

When `ENVIRONMENT` is `staging` or `production`, settings also require `SECRET_KEY`, redirect
to HTTPS behind the proxy's `X-Forwarded-Proto`, send HSTS and mark the session and CSRF
cookies secure.

## The four apps

| App | What it owns |
| --- | --- |
| `accounts` | Email-based `CustomUser`, JWT and session auth, an admin → client → customer hierarchy |
| `rental` | Spreadsheet ingest, the AirDNA batch, the profit maths, filters, CSV export |
| `lease` | Leases, versioned documents on Cloudinary, the structured review, chat over it |
| `regulations` | One saved STR-legality question per row, its answer, chat over it |

```mermaid
flowchart LR
    subgraph apps[Domain apps]
        accounts
        rental
        lease
        regulations
    end
    subgraph core[bnbu_core]
        llm[LLM provider registry<br/>openai / demo / null]
        sources[Regulation source chain<br/>curated / llm]
        scope[Owner scoping, pagination,<br/>day ranges, health]
    end
    constants[bnbu_constants<br/>AirDNA client, profit maths]
    rental --> constants
    lease --> llm
    regulations --> sources
    sources --> llm
    apps --> scope
    worker[(Celery worker)] -.runs tasks of.-> rental & lease & regulations
```

A `CustomUser` owns leases and regulation searches by foreign key and may be a client with
customers beneath it (a self-FK); a lease has versioned documents and mirrors the verdict of
its newest. `RentalProperty` keeps its owner in a plain integer column rather than a
relation, so its index is declared explicitly and its permission class compares ids.

Each app has a `services.py` holding its use cases; a DRF action parses, calls one service
function, and renders. `bnbu_core/` holds what all four need — the language-model registry,
the regulation-source registry, owner scoping, pagination, day-range filtering, JSON salvage
for legacy rows, health endpoints, `seed_demo` — and imports none of the four apps, so
dependencies point inward. `bnbu_constants/` holds the profit maths, the AirDNA client and the
spreadsheet column contract. A property AirDNA cannot price is `Error` with a **null** profit,
never `$0.00` and `Rejected`: `nan >= 1000` is `False`, so a verdict used to be reported on an
investment nothing was known about (`test_unknown_profit_is_an_error_not_a_rejection`).

The spreadsheet contract is the Zillow export: `Location`, `Price`, `Sq. ft.`, `Ba`, `Br`,
`Link`, as `.csv` or `.xlsx`. Numbers are read through thousands separators — `"1,769 sqft"`
used to be stored as 1 square foot.

The OpenAI SDK appears in exactly one file, `bnbu_core/llm/openai_provider.py`. Moving it from
the 0.28 module API to the current client touched nothing else, and any vendor failure now
leaves that file as an `LLMError`, which the callers already turn into a `503`.

## Queries, payloads, indexes

- **The lease list.** `num_of_docs` is a model method, so serializing a page of ten leases
  issued ten `COUNT`s on top of ten more for the nested documents. `get_base_queryset`
  annotates the count — shadowing the method — and `Prefetch`es the documents, so a page
  costs one query for the page, one for its documents, one for the pagination count. Nested
  documents use `DocumentSummarySerializer`, keeping full review text and chat transcripts
  on the document routes.
- **Seven named indexes**: `{lease,regulation,rental}_user_recent_idx` for "this user's rows,
  newest first", `rental_status_profit_idx` for the profit filter,
  `document_lease_version_idx` for version lookups, and a status index on leases and
  regulations.
- **Nothing unbounded.** Every list route paginates (`page_size` up to 100), `batch_ids()`
  collects distinct batches with `DISTINCT` in SQL, the CSV export iterates with
  `.iterator(chunk_size=500)` so a `StreamingHttpResponse` is not quietly materialising
  every row first, and `LEASE_MAX_CHUNKS` (12) caps the five-page chunks per review while
  chat replays 20 turns, not a whole history.
- **Date filters are whole days.** `start_date`/`end_date` become aware midnights with an
  exclusive end (`bnbu_core/dates.py`); the end bound used to include rows stamped at the
  following midnight.
- **Workers lose nothing.** `task_acks_late` with `worker_prefetch_multiplier=1` hands an
  interrupted review back to the queue, under a 540-second soft limit.

## Running it

```bash
docker compose up --build
```

That waits for Postgres, migrates, collects static files, seeds
(`Seeded 8 properties, 3 leases and 4 regulation searches.`) and starts the API and a Celery
worker, all four services (`db`, `redis`, `api`, `worker`) healthchecked, from a multi-stage,
non-root image.

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
curl -s 'http://localhost:8131/api/rental_properties/?page_size=2' -H "Authorization: Bearer $TOKEN"
```

**No API keys are needed.** Compose sets `LLM_PROVIDER=demo`, which returns deterministic
payloads through the same parsing, validation and persistence code the live pipeline uses, and
`/api/ready/` reports which integrations are wired up while still answering `ready` with none
of them. Set `OPENAI_API_KEY` and drop `LLM_PROVIDER` to go live, and `docker compose down -v`
to tear it down.

### Configuration

Everything is read from the environment, a `.env` file is loaded automatically, and every
integration is optional. [`.env.example`](.env.example) lists every variable the code reads.
`DATABASE_URL` (any `dj-database-url` URL) and `REDIS_URL` (`rediss://` for TLS) point at the
infrastructure, `ENVIRONMENT=staging|production` tightens `ALLOWED_HOSTS`, makes `SECRET_KEY`
mandatory and turns on the HTTPS settings, `CORS_ALLOWED_ORIGINS` is a **JSON array**, and the
model path is tuned by `LLM_PROVIDER`, `LLM_MODEL`, `LLM_MODEL_OVERRIDES` (purpose → model, so
chat can run cheap while extraction keeps the strong one), `LEASE_MAX_CHUNKS`,
`REGULATION_SOURCES` and `REGULATION_CACHE_TTL`. `OPENAI_API_KEY`, `AIRDNA_*`, `CLOUDINARY_*`
and the ClickUp variables are each needed only by the feature that uses them — a missing
`CLICKUP_URL` skips that push and logs it.

## Without Docker

Python 3.12 (`.python-version`), with PostgreSQL and Redis running locally. Redis is the
Django cache as well as the Celery broker, so the API and `seed_demo` need it too, not only
the worker.

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt     # runtime pins plus black, isort, flake8
cp .env.example .env                    # LLM_PROVIDER=demo, local Postgres and Redis
python manage.py migrate && python manage.py seed_demo   # seeding is idempotent
python manage.py runserver
celery -A bnbu_backend_api worker -l info                # second shell

python manage.py test                   # 250 tests
python manage.py makemigrations --check --dry-run
isort --check-only . && black --check . && flake8 .
```

**No test can make a real OpenAI call**: the default provider under test is `null`, so a
forgotten mock fails loudly instead of billing, and tests that need a model install a
`ScriptedProvider` through the same registry the application uses — exercising real prompt
building, parsing and persistence rather than a mock's return value. The OpenAI provider's
own tests drive the real SDK client through an in-process mock transport. The cache is
in-memory and Celery runs tasks inline, so the suite needs a database and nothing else; it
passes on PostgreSQL and on SQLite (`DATABASE_URL=sqlite:///test.sqlite3`).

## What this does not do

- **The curated regulation table is six illustrative cities**, summarised from their
  ordinances so the chain has a working first source and the demo answers with no API key.
  Not legal advice, and not maintained against amendments.
- **No RAG.** Regulation answers are the model's own knowledge plus that table. A retrieval
  index over municipal codes is the next step and the reason `RegulationSource` exists; a
  vector store with no corpus behind it would be theatre.
- **Text PDFs only** — a scanned lease yields no extractable text, is reported as such, and
  gets no OCR. Spreadsheets are `.csv` or `.xlsx`; legacy `.xls` is refused.
- **Lease and regulation chat are still synchronous.** Interactive, so a queue would not
  help the user, but they hold a worker for a whole completion; streaming is the right fix
  and is not done. Nor is throttling — DRF throttling is unconfigured, so a busy account can
  queue as many reviews as it likes.
- **Batch ids are `MAX + 1`**, so two uploads landing in the same instant can share one.
- **The demo provider is a fixture, not a model.** Under `LLM_PROVIDER=demo` every lease
  produces the same four clause findings — there to show the product working, not analysis.

## License

[MIT](LICENSE).
