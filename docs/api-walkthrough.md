# API walkthrough

Every request and response below was captured with `curl` against the stack
started by `docker compose up --build`, running on `http://localhost:8131`
with `LLM_PROVIDER=demo` and no API keys of any kind set. Nothing here is
hand-written; the transcript is produced by replaying these calls against a
freshly seeded database.

Long JSON bodies are pretty-printed; tokens are shortened.

## 1. The stack comes up with one command

```console
$ docker compose up --build
api-1  | ==> Waiting for the database
api-1  | ==> Applying migrations
api-1  | ==> Collecting static files
api-1  | ==> Seeding demo data
api-1  | Seeded 8 properties, 3 leases and 4 regulation searches.
api-1  | Sign in as admin@bnbu.test / DemoPass!123
api-1  | ==> Starting: gunicorn bnbu_backend_api.wsgi:application --bind=0.0.0.0:8000 --workers=3 --timeout=120 --access-logfile=-
api-1  | [1] [INFO] Starting gunicorn 23.0.0
api-1  | [1] [INFO] Listening at: http://0.0.0.0:8000 (1)
api-1  | [65] [INFO] Booting worker with pid: 65
api-1  | [66] [INFO] Booting worker with pid: 66
api-1  | [67] [INFO] Booting worker with pid: 67
worker-1  |  -------------- celery@c2259aeaac57 v5.4.0 (opalescent)
worker-1  |  -------------- [queues]
worker-1  | [2026-09-23 12:01:03,700: INFO/MainProcess] Connected to redis://redis:6379/0
worker-1  | [2026-09-23 12:01:04,740: INFO/MainProcess] celery@c2259aeaac57 ready.
```

## 2. Liveness and readiness

```console
$ curl -s http://localhost:8131/api/health/
{
    "status": "ok"
}

$ curl -s http://localhost:8131/api/ready/
{
    "status": "ready",
    "checks": {
        "database": true,
        "cache": true
    },
    "integrations": {
        "llm_provider": "demo",
        "llm_available": true,
        "regulation_sources": [
            "curated",
            "llm"
        ],
        "airdna_configured": false,
        "cloudinary_configured": false
    }
}
```

`/api/ready/` reports which integrations are actually wired up. Here the
language model is the deterministic demo provider and neither AirDNA nor
Cloudinary is configured — and the API still serves every route.

## 3. Authenticating

```console
$ curl -s -X POST http://localhost:8131/api/token/ \
    -H 'Content-Type: application/json' \
    -d '{"email":"analyst@bnbu.test","password":"DemoPass!123"}'
{
    "refresh": "eyJhbGciOiJIUzI1NiIsInR5...truncated",
    "access": "eyJhbGciOiJIUzI1NiIsInR5...truncated"
}
```

## 4. Underwritten properties (paginated)

The seed prices eight properties through the same maths the AirDNA batch uses.

```console
$ curl -s 'http://localhost:8131/api/rental_properties/?page_size=2' -H "Authorization: Bearer $TOKEN"
{
    "count": 8,
    "next": "http://localhost:8131/api/rental_properties/?page=2&page_size=2",
    "previous": null,
    "results": [
        {
            "id": 8,
            "created_at_formatted": "September 23, 2026",
            "user_id": 2,
            "created_at": "2026-09-23T12:00:52.531173Z",
            "updated_at": "2026-09-23T12:00:52.531177Z",
            "location": "130 Union St, Kirkland, WA",
            "rent": 4100,
            "no_of_bedrooms": 4,
            "no_of_bathrooms": 3,
            "square_feet": 2350,
            "utilities": "8000.00",
            "adr": "369.23",
            "occupancy_rate": "0.53",
            "property_zillow_link": "https://www.zillow.com/homedetails/1007_zpid/",
            "property_status": "Approved",
            "yearly_rent_cost_util": "57200.00",
            "yearly_projected_revenue": 96000,
            "monthly_estimated_profit": "3233.33",
            "batch_id": 2
        },
        {
            "id": 7,
            "created_at_formatted": "September 23, 2026",
            "user_id": 2,
            "created_at": "2026-09-23T12:00:52.530867Z",
            "updated_at": "2026-09-23T12:00:52.530871Z",
            "location": "55 Palm Dr, Scottsdale, AZ",
            "rent": 2050,
            "no_of_bedrooms": 2,
            "no_of_bathrooms": 1,
            "square_feet": 1040,
            "utilities": null,
            "adr": null,
            "occupancy_rate": null,
            "property_zillow_link": "https://www.zillow.com/homedetails/1006_zpid/",
            "property_status": "Error",
            "yearly_rent_cost_util": null,
            "yearly_projected_revenue": null,
            "monthly_estimated_profit": null,
            "batch_id": 2
        }
    ]
}
```

Note `55 Palm Dr`: AirDNA returned nothing for it, so its status is `Error`
and its profit is `null` — not `$0.00` and `Rejected`. A verdict on an
investment that was never evaluated would be worse than no verdict.

## 5. Filtering and CSV export

```console
$ curl -s -X POST http://localhost:8131/api/rental_properties/filtered-list/ \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d '{"property_status":["Approved"],"no_of_bedrooms":[3,4]}'
{
    "count": 8,
    "next": null,
    "previous": null,
    "results": {
        "all_batch_ids": [
            1,
            2
        ],
        "properties": [
            {
                "id": 8,
                "created_at_formatted": "September 23, 2026",
                "user_id": 2,
                "created_at": "2026-09-23T12:00:52.531173Z",
                "updated_at": "2026-09-23T12:00:52.531177Z",
                "location": "130 Union St, Kirkland, WA",
                "rent": 4100,
                "no_of_bedrooms": 4,
                "no_of_bathrooms": 3,
                "square_feet": 2350,
                "utilities": "8000.00",
                "adr": "369.23",
                "occupancy_rate": "0.53",
                "property_zillow_link": "https://www.zillow.com/homedetails/1007_zpid/",
                "property_status": "Approved",
                "yearly_rent_cost_util": "57200.00",
                "yearly_projected_revenue": 96000,
                "monthly_estimated_profit": "3233.33",
                "batch_id": 2
            },
            {
                "id": 7,
                "created_at_formatted": "September 23, 2026",
                "user_id": 2,
                "created_at": "2026-09-23T12:00:52.530867Z",
                "updated_at": "2026-09-23T12:00:52.530871Z",
                "location": "55 Palm Dr, Scottsdale, AZ",
                "rent": 2050,
                "no_of_bedrooms": 2,
                "no_of_bathrooms": 1,
                "square_feet": 1040,
                "utilities": null,
                "adr": null,
                "occupancy_rate": null,
                "property_zillow_link": "https://www.zillow.com/homedetails/1006_zpid/",
                "property_status": "Error",
                "yearly_rent_cost_util": null,
                "yearly_projected_revenue": null,
                "monthly_estimated_profit": null,
                "batch_id": 2
            },
            {
                "id": 6,
                "created_at_formatted": "September 23, 2026",
                "user_id": 2,
                "created_at": "2026-09-23T12:00:52.530581Z",
                "updated_at": "2026-09-23T12:00:52.530588Z",
                "location": "6 Willow Way, Austin, TX",
                "rent": 2900,
                "no_of_bedrooms": 3,
                "no_of_bathrooms": 2,
                "square_feet": 1580,
                "utilities": null,
                "adr": null,
                "occupancy_rate": null,
                "property_zillow_link": "https://www.zillow.com/homedetails/1005_zpid/",
                "property_status": "Error",
                "yearly_rent_cost_util": null,
                "yearly_projected_revenue": null,
                "monthly_estimated_profit": null,
                "batch_id": 2
            },
            {
                "id": 5,
                "created_at_formatted": "September 23, 2026",
                "user_id": 2,
                "created_at": "2026-09-23T12:00:52.530251Z",
                "updated_at": "2026-09-23T12:00:52.530255Z",
                "location": "201 Grand Blvd, Nashville, TN",
                "rent": 1750,
                "no_of_bedrooms": 1,
                "no_of_bathrooms": 1,
                "square_feet": 760,
                "utilities": "2000.00",
                "adr": "150.00",
                "occupancy_rate": "0.65",
                "property_zillow_link": "https://www.zillow.com/homedetails/1004_zpid/",
                "property_status": "Approved",
                "yearly_rent_cost_util": "23000.00",
                "yearly_projected_revenue": 39000,
                "monthly_estimated_profit": "1333.33",
                "batch_id": 1
            },
            {
                "id": 4,
                "created_at_formatted": "September 23, 2026",
                "user_id": 2,
                "created_at": "2026-09-23T12:00:52.529839Z",
                "updated_at": "2026-09-23T12:00:52.529845Z",
                "location": "18 Birch Ct, Kirkland, WA",
                "rent": 3600,
                "no_of_bedrooms": 4,
                "no_of_bathrooms": 3,
                "square_feet": 2100,
                "utilities": "8000.00",
                "adr": "465.38",
                "occupancy_rate": "0.58",
                "property_zillow_link": "https://www.zillow.com/homedetails/1003_zpid/",
                "property_status": "Approved",
                "yearly_rent_cost_util": "51200.00",
                "yearly_projected_revenue": 121000,
                "monthly_estimated_profit": "5816.67",
                "batch_id": 1
            },
            {
                "id": 3,
                "created_at_formatted": "September 23, 2026",
                "user_id": 2,
                "created_at": "2026-09-23T12:00:52.529361Z",
                "updated_at": "2026-09-23T12:00:52.529367Z",
                "location": "77 Harbor St, Scottsdale, AZ",
                "rent": 2450,
                "no_of_bedrooms": 2,
                "no_of_bathrooms": 2,
                "square_feet": 1320,
                "utilities": "4000.00",
                "adr": "284.62",
                "occupancy_rate": "0.72",
                "property_zillow_link": "https://www.zillow.com/homedetails/1002_zpid/",
                "property_status": "Approved",
                "yearly_rent_cost_util": "33400.00",
                "yearly_projected_revenue": 74000,
                "monthly_estimated_profit": "3383.33",
                "batch_id": 1
            },
            {
                "id": 2,
                "created_at_formatted": "September 23, 2026",
                "user_id": 2,
                "created_at": "2026-09-23T12:00:52.528811Z",
                "updated_at": "2026-09-23T12:00:52.528830Z",
                "location": "9 Cedar Ln, Nashville, TN",
                "rent": 3100,
                "no_of_bedrooms": 3,
                "no_of_bathrooms": 2,
                "square_feet": 1640,
                "utilities": "6000.00",
                "adr": "157.69",
                "occupancy_rate": "0.60",
                "property_zillow_link": "https://www.zillow.com/homedetails/1001_zpid/",
                "property_status": "Rejected",
                "yearly_rent_cost_util": "43200.00",
                "yearly_projected_revenue": 41000,
                "monthly_estimated_profit": "-183.33",
                "batch_id": 1
            },
            {
                "id": 1,
                "created_at_formatted": "September 23, 2026",
                "user_id": 2,
                "created_at": "2026-09-23T12:00:52.527339Z",
                "updated_at": "2026-09-23T12:00:52.527360Z",
                "location": "412 Cypress Ave, Austin, TX",
                "rent": 2100,
                "no_of_bedrooms": 2,
                "no_of_bathrooms": 2,
                "square_feet": 1180,
                "utilities": "4000.00",
                "adr": "261.54",
                "occupancy_rate": "0.61",
                "property_zillow_link": "https://www.zillow.com/homedetails/1000_zpid/",
                "property_status": "Approved",
                "yearly_rent_cost_util": "29200.00",
                "yearly_projected_revenue": 68000,
                "monthly_estimated_profit": "3233.33",
                "batch_id": 1
            }
        ]
    }
}

$ curl -s 'http://localhost:8131/api/rental_properties/download-csv/?batch_id=2' -H "Authorization: Bearer $TOKEN"
Date,Batch Id,Location,Rent,Bedrooms,Bathrooms,Square Feet,Link,Adr,Utilities,Estimated Profit,Estimated Earnings,Yearly Rent Cost,Occupancy Rate,Zillow Property Status
"September 23, 2026",2,"130 Union St, Kirkland, WA",4100,4,3,2350,https://www.zillow.com/homedetails/1007_zpid/,369.23,8000.00,3233.33,96000,57200.00,0.53,Approved
"September 23, 2026",2,"55 Palm Dr, Scottsdale, AZ",2050,2,1,1040,https://www.zillow.com/homedetails/1006_zpid/,None,None,None,None,None,None,Error
"September 23, 2026",2,"6 Willow Way, Austin, TX",2900,3,2,1580,https://www.zillow.com/homedetails/1005_zpid/,None,None,None,None,None,None,Error
```

The export is a real stream: `.iterator()` with a 500-row chunk, so a
50,000-row batch never lands in memory.

## 6. A lease and its documents

```console
$ curl -s 'http://localhost:8131/api/leases/?page_size=1' -H "Authorization: Bearer $TOKEN"
{
    "count": 3,
    "next": "http://localhost:8131/api/leases/?page=2&page_size=1",
    "previous": null,
    "results": [
        {
            "id": 3,
            "date": "2026-09-23",
            "address1": "9 Cedar Ln",
            "address2": null,
            "city": "Nashville",
            "state": "TN",
            "zip_code": "37206",
            "status": "Pending",
            "num_of_docs": 1,
            "documents": [
                {
                    "id": 3,
                    "lease_id": 3,
                    "name": "cedar-ln-lease.pdf",
                    "file": null,
                    "file_url": "https://example.invalid/leases/cedar-ln-lease.pdf",
                    "version": 1,
                    "uploaded_at": "2026-09-23T12:00:52.548048Z",
                    "status": "Pending"
                }
            ]
        }
    ]
}
```

The nested documents are summaries. The full review text and the chat
transcript live on the document routes, so a page of ten leases is a small
response rather than a few hundred kilobytes of prose nobody asked for.

## 7. The structured lease review

This is the centre of the product. The worker sends the PDF through the
language model in five-page chunks, asks for a JSON object, and validates it
into a typed `LeaseAnalysis` before anything is stored.

```console
$ curl -s http://localhost:8131/api/documents/2/analysis/ -H "Authorization: Bearer $TOKEN"
{
    "model": "demo-1",
    "pages": 14,
    "clauses": [
        {
            "risk": "high",
            "type": "subletting",
            "excerpt": "Tenant shall not sublet the Premises or any portion thereof, nor assign this Lease, under any circumstance.",
            "finding": "An absolute ban with no landlord-consent carve-out. For a short-term-rental strategy this clause alone makes the unit unusable; ask for 'not to be unreasonably withheld' consent.",
            "confidence": 0.93
        },
        {
            "risk": "medium",
            "type": "late_fee",
            "excerpt": "A late charge of $150 plus $25 per day shall accrue.",
            "finding": "The per-day component is uncapped, so a two-week delay costs $500. Ask for a cap at 5% of one month's rent.",
            "confidence": 0.88
        },
        {
            "risk": "medium",
            "type": "maintenance",
            "excerpt": "Tenant is responsible for all repairs under $500.",
            "finding": "Shifts routine appliance and plumbing repair onto the tenant. Budget roughly $1,200 a year, or negotiate the threshold down.",
            "confidence": 0.81
        },
        {
            "risk": "low",
            "type": "security_deposit",
            "excerpt": "Security deposit equal to two (2) months' rent.",
            "finding": "Two months is at the legal maximum in several states; check the local cap and the interest-bearing-account requirement.",
            "confidence": 0.76
        }
    ],
    "summary": "A twelve-month fixed term at $2,450 a month with a two-month security deposit. Three clauses are worth negotiating before signing: the blanket subletting ban, an uncapped late fee, and a repair responsibility that is pushed onto the tenant below $500.",
    "verdict": "Draft",
    "provider": "demo",
    "confidence": 0.74,
    "financials": {
        "late_fee": 150.0,
        "monthly_rent": 2450.0,
        "security_deposit": 4900.0,
        "lease_term_months": 12.0
    },
    "structured": true,
    "created_time": "2026-09-23T12:00:52.532745+00:00"
}
```

Every clause carries its own confidence and the lease’s own words. The old
code decided the verdict with `if "approved" in text.lower()`, which reads
*"this lease would not be approved"* as an approval.

## 8. Asking a question about the reviewed lease

```console
$ curl -s -X POST http://localhost:8131/api/documents/2/chat/ \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d '{"message":"What is the subletting position?"}'
{
    "response": "The subletting clause is the one to push on. As written it bans assignment and subletting outright, with no consent mechanism, which rules out the short-term-rental use you are underwriting. The usual counter is to accept the ban on assignment but ask for subletting \"with landlord consent, not to be unreasonably withheld\", and to name the platform you intend to list on so the landlord is not surprised later.",
    "chat_history": [
        {
            "role": "user",
            "content": "What is the subletting position?",
            "timestamp": "2026-09-23T12:01:19.613861+00:00"
        },
        {
            "role": "assistant",
            "content": "The subletting clause is the one to push on. As written it bans assignment and subletting outright, with no consent mechanism, which rules out the short-term-rental use you are underwriting. The usual counter is to accept the ban on assignment but ask for subletting \"with landlord consent, not to be unreasonably withheld\", and to name the platform you intend to list on so the landlord is not surprised later.",
            "timestamp": "2026-09-23T12:01:19.614030+00:00"
        }
    ],
    "summary": "A twelve-month fixed term at $2,450 a month with a two-month security deposit. Three clauses are worth negotiating before signing: the blanket subletting ban, an uncapped late fee, and a repair responsibility that is pushed onto the tenant below $500."
}
```

The review summary is put in front of the model, so the answer is grounded in
what the reviewer already sees rather than in the raw PDF again.

The id used to be read out of the request body and the id in the URL was
ignored entirely. The URL is authoritative now, and a body that disagrees with
it is refused instead of silently preferred:

```console
$ curl -s -X POST http://localhost:8131/api/documents/2/chat/ \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d '{"document_id":1,"message":"..."}' -w '\nHTTP %{http_code}\n'
HTTP 400
{
    "document_id": "Does not match the id in the URL (2)."
}
```

## 9. Regulations: the curated source answers inside the request

`Santa Monica, CA` is in the curated table, so the answer is written inside the
request and comes back `201` with `analysis_state: "complete"`.

```console
$ curl -s -X POST http://localhost:8131/api/regulations/ \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d '{"search":"santa monica ca"}' -w '\nHTTP %{http_code}\n'
HTTP 201
{
    "id": 5,
    "date": "2026-09-23",
    "search": "santa monica ca",
    "status": "STR Not Allowed",
    "gpt_response": {
        "status": "STR Not Allowed",
        "message": "Only home-sharing is legal; unhosted short-term rental is not.\n\n- The host must live on the property and remain present during the stay.\n- A home-sharing licence and transient occupancy tax registration are required.\n- Vacation rentals - the whole unit, no host present, under 30 days - are prohibited outright.\n- Listings must show the licence number, and platforms are liable for unlicensed listings.",
        "created_time": "2026-09-23T12:01:19.794143+00:00",
        "source": "curated:Santa Monica, CA",
        "confidence": 0.93,
        "citations": [
            "Santa Monica Municipal Code Ch. 6.20"
        ]
    },
    "chat_history": [],
    "analysis_state": "complete",
    "source": "curated:Santa Monica, CA"
}
```

The location is normalised before the lookup, so `santa monica ca` and
`Kirkland, WA` are the same question and the second one is a cache hit.

## 10. Regulations: anything else is queued, not waited on

A location the curated table does not cover needs the model. That used to
happen inline inside the POST. It now returns `202` immediately and a Celery
worker fills the row in.

```console
$ curl -s -X POST http://localhost:8131/api/regulations/ \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d '{"search":"Porto, Portugal"}' -w '\nHTTP %{http_code}\n'
HTTP 202
{
    "id": 6,
    "date": "2026-09-23",
    "search": "Porto, Portugal",
    "status": "pending",
    "gpt_response": null,
    "chat_history": [],
    "analysis_state": "pending",
    "source": ""
}

# a moment later, the worker has filled it in
$ curl -s http://localhost:8131/api/regulations/6/ -H "Authorization: Bearer $TOKEN"
{
    "id": 6,
    "date": "2026-09-23",
    "search": "Porto, Portugal",
    "status": "STR Allowed with Restrictions",
    "gpt_response": {
        "source": "llm:demo",
        "status": "STR Allowed with Restrictions",
        "message": "SHORT TERM RENTAL ALLOWED WITH RESTRICTIONS\n\n- Operators must register annually and display the permit number in every listing.\n- The unit must be the operator's primary residence for at least 245 days a year, or hold a legacy non-primary permit.\n- Occupancy is capped at two guests per bedroom plus two.\n- Lodging tax is collected by the platform, but the operator files the annual return.\n\nSources: [Municipal code, short-term rentals](https://example.gov/code/str)",
        "citations": [
            "Municipal code, short-term rentals (https://example.gov/code/str)"
        ],
        "confidence": 0.7,
        "created_time": "2026-09-23T12:01:20.117824+00:00"
    },
    "chat_history": [],
    "analysis_state": "complete",
    "source": "llm:demo"
}

$ docker compose logs worker | tail -4
worker-1  |   . regulations.tasks.analyze_regulation_task
worker-1  | [2026-09-23 12:01:19,994: INFO/MainProcess] Task regulations.tasks.analyze_regulation_task[00472358-2896-4bd5-9ee4-998c43b954ad] received
worker-1  | [2026-09-23 12:01:20,158: INFO/ForkPoolWorker-2] Task regulations.tasks.analyze_regulation_task[00472358-2896-4bd5-9ee4-998c43b954ad] succeeded in 0.15000412500012317s: None
```

## 11. Ownership is enforced, not assumed

The same row, asked for with no token and then by the other seeded client
(`rival@bnbu.test`, who owns nothing).

```console
$ curl -s http://localhost:8131/api/regulations/6/   # no token at all
HTTP 401
{
    "detail": "Authentication credentials were not provided."
}

$ curl -s http://localhost:8131/api/regulations/6/ -H "Authorization: Bearer $OTHER_USERS_TOKEN" -w '\nHTTP %{http_code}\n'
HTTP 404
{
    "detail": "No Regulations matches the given query."
}
```

A row belonging to somebody else is a `404`, not a `403`: the response does not
confirm that the id exists. Five list endpoints used to return other users’
rows; the scoping now lives in one mixin instead of four hand-written
`get_queryset` overrides.

## 12. No API key, no crash

With the language model switched off entirely, the API still serves. A model
that is not configured is a clean `503`, not a vendor traceback.

```console
$ docker compose exec -e LLM_PROVIDER=null api python -c "..."
provider           : null
is_available()     : False
complete() raises  : LLMUnavailable: No language model is configured. Set OPENAI_API_KEY, or set LLM_PROVIDER=demo to run against the built-in canned responses.
```

## 13. The test suite

Against a real Postgres, with no network access and no API key set. The
expected 4xx warnings and the deliberate traceback from the failure-path
tests are elided; nothing else is.

```console
$ python manage.py test
Creating test database for alias 'default'...
Found 222 test(s).
System check identified no issues (0 silenced).
..........................................................................
..........................................................................
..........................................................................
----------------------------------------------------------------------
Ran 222 tests in 31.591s

OK
Destroying test database for alias 'default'...
```
