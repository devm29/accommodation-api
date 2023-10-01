# API walkthrough

Every request and response below was captured with `curl` against the stack started by
`docker compose up --build` on `http://localhost:8131`, with `LLM_PROVIDER=demo` and no API
keys of any kind set, on a freshly seeded database. JSON bodies are pretty-printed and
tokens are shortened; nothing else is edited.

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
api-1  | [2026-11-07 21:49:15 +0000] [1] [INFO] Starting gunicorn 26.2.0
api-1  | [2026-11-07 21:49:15 +0000] [1] [INFO] Listening at: http://0.0.0.0:8000 (1)
worker-1  | ==> Waiting for the database
worker-1  | ==> Applying migrations
worker-1  | ==> Starting: celery -A bnbu_backend_api worker -l info --concurrency=2
worker-1  |  -------------- celery@acdb083a4e91 v5.7.0 (collider)
worker-1  | [2026-11-07 21:49:22,886: INFO/MainProcess] Connected to redis://:**@redis:6379/0
worker-1  | [2026-11-07 21:49:23,922: INFO/MainProcess] celery@acdb083a4e91 ready.
worker-1  | [2026-11-07 21:49:32,448: INFO/ForkPoolWorker-2] Task regulations.tasks.analyze_regulation_task[82115cb8-bd68-4ff8-bab1-bd561b3e15c3] succeeded in 0.022119790999568067s: None
worker-1  | [2026-11-07 21:49:36,609: INFO/ForkPoolWorker-2] Task regulations.tasks.analyze_regulation_task[5e5b38d7-dacb-4d09-9022-6807574d5383] succeeded in 0.009074957997654565s: None
```

## 2. Liveness and readiness

`/api/health/` touches nothing; `/api/ready/` checks the database and the cache and reports the optional integrations without failing on their absence.

```console
$ curl -s http://localhost:8131/api/health/
HTTP 200
{
  "status": "ok"
}
```

```console
$ curl -s http://localhost:8131/api/ready/
HTTP 200
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

## 3. Authenticating

Tokens are shortened to their first six and last four characters. The examples below use `$TOKEN` for the analyst's access token and `$RIVAL_TOKEN` for `rival@bnbu.test`, who owns nothing.

```console
$ curl -s -X POST http://localhost:8131/api/token/ -H 'Content-Type: application/json' -d '{"email":"analyst@bnbu.test","password":"DemoPass!123"}'
{
  "refresh": "eyJhbG...zgbM",
  "access": "eyJhbG...thjc"
}
```

## 4. Underwritten properties (paginated)

`55 Palm Dr` is a property AirDNA could not price: status `Error`, every derived number `null`, never a `$0.00` profit.

```console
$ curl -s 'http://localhost:8131/api/rental_properties/?page_size=2' -H "Authorization: Bearer $TOKEN"
HTTP 200
{
  "count": 8,
  "next": "http://localhost:8131/api/rental_properties/?page=2&page_size=2",
  "previous": null,
  "results": [
    {
      "id": 8,
      "created_at_formatted": "November 07, 2026",
      "user_id": 2,
      "created_at": "2026-11-07T21:49:14.580518Z",
      "updated_at": "2026-11-07T21:49:14.580520Z",
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
      "created_at_formatted": "November 07, 2026",
      "user_id": 2,
      "created_at": "2026-11-07T21:49:14.580289Z",
      "updated_at": "2026-11-07T21:49:14.580292Z",
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

## 5. Filtering and CSV export

The filter takes a JSON body, the export the same filters as a query string. The export streams.

```console
$ curl -s -X POST 'http://localhost:8131/api/rental_properties/filtered-list/?page_size=2' -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"status":"Approved","min_profit":3000}'
HTTP 200
{
  "count": 4,
  "next": "http://localhost:8131/api/rental_properties/filtered-list/?page=2&page_size=2",
  "previous": null,
  "results": {
    "all_batch_ids": [
      1,
      2
    ],
    "properties": [
      {
        "id": 8,
        "created_at_formatted": "November 07, 2026",
        "user_id": 2,
        "created_at": "2026-11-07T21:49:14.580518Z",
        "updated_at": "2026-11-07T21:49:14.580520Z",
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
        "id": 4,
        "created_at_formatted": "November 07, 2026",
        "user_id": 2,
        "created_at": "2026-11-07T21:49:14.579532Z",
        "updated_at": "2026-11-07T21:49:14.579534Z",
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
      }
    ]
  }
}
```

```console
$ curl -s "http://localhost:8131/api/rental_properties/download-csv/?status=Approved" -H "Authorization: Bearer $TOKEN"
Date,Batch Id,Location,Rent,Bedrooms,Bathrooms,Square Feet,Link,Adr,Utilities,Estimated Profit,Estimated Earnings,Yearly Rent Cost,Occupancy Rate,Zillow Property Status
"November 07, 2026",2,"130 Union St, Kirkland, WA",4100,4,3,2350,https://www.zillow.com/homedetails/1007_zpid/,369.23,8000.00,3233.33,96000,57200.00,0.53,Approved
"November 07, 2026",1,"201 Grand Blvd, Nashville, TN",1750,1,1,760,https://www.zillow.com/homedetails/1004_zpid/,150.00,2000.00,1333.33,39000,23000.00,0.65,Approved
"November 07, 2026",1,"18 Birch Ct, Kirkland, WA",3600,4,3,2100,https://www.zillow.com/homedetails/1003_zpid/,465.38,8000.00,5816.67,121000,51200.00,0.58,Approved
"November 07, 2026",1,"77 Harbor St, Scottsdale, AZ",2450,2,2,1320,https://www.zillow.com/homedetails/1002_zpid/,284.62,4000.00,3383.33,74000,33400.00,0.72,Approved
"November 07, 2026",1,"412 Cypress Ave, Austin, TX",2100,2,2,1180,https://www.zillow.com/homedetails/1000_zpid/,261.54,4000.00,3233.33,68000,29200.00,0.61,Approved
```

## 6. A lease and its documents

Nested documents are summaries; the review text and chat stay on the document routes. Lease 3's only document is still queued for review, and the lease stays a `Draft` rather than taking on the document's `Pending`.

```console
$ curl -s 'http://localhost:8131/api/leases/?page_size=1' -H "Authorization: Bearer $TOKEN"
HTTP 200
{
  "count": 3,
  "next": "http://localhost:8131/api/leases/?page=2&page_size=1",
  "previous": null,
  "results": [
    {
      "id": 3,
      "date": "2026-11-07",
      "address1": "9 Cedar Ln",
      "address2": null,
      "city": "Nashville",
      "state": "TN",
      "zip_code": "37206",
      "status": "Draft",
      "num_of_docs": 1,
      "documents": [
        {
          "id": 3,
          "lease_id": 3,
          "name": "cedar-ln-lease.pdf",
          "file": null,
          "file_url": "https://example.invalid/leases/cedar-ln-lease.pdf",
          "version": 1,
          "uploaded_at": "2026-11-07T21:49:14.587407Z",
          "status": "Pending"
        }
      ]
    }
  ]
}
```

## 7. The structured lease review

```console
$ curl -s http://localhost:8131/api/documents/2/analysis/ -H "Authorization: Bearer $TOKEN"
HTTP 200
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
  "created_time": "2026-11-07T21:49:14.581323+00:00"
}
```

## 8. Asking a question about the reviewed lease

```console
$ curl -s -X POST http://localhost:8131/api/documents/2/chat/ -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"message":"Can I sublet on Airbnb?"}'
HTTP 200
{
  "response": "The subletting clause is the one to push on. As written it bans assignment and subletting outright, with no consent mechanism, which rules out the short-term-rental use you are underwriting. The usual counter is to accept the ban on assignment but ask for subletting \"with landlord consent, not to be unreasonably withheld\", and to name the platform you intend to list on so the landlord is not surprised later.",
  "chat_history": [
    {
      "role": "user",
      "content": "Can I sublet on Airbnb?",
      "timestamp": "2026-11-07T21:49:32.181435+00:00"
    },
    {
      "role": "assistant",
      "content": "The subletting clause is the one to push on. As written it bans assignment and subletting outright, with no consent mechanism, which rules out the short-term-rental use you are underwriting. The usual counter is to accept the ban on assignment but ask for subletting \"with landlord consent, not to be unreasonably withheld\", and to name the platform you intend to list on so the landlord is not surprised later.",
      "timestamp": "2026-11-07T21:49:32.181461+00:00"
    }
  ],
  "summary": "A twelve-month fixed term at $2,450 a month with a two-month security deposit. Three clauses are worth negotiating before signing: the blanket subletting ban, an uncapped late fee, and a repair responsibility that is pushed onto the tenant below $500."
}
```

## 9. The review is the server's to write

`status` is read-only on the document routes, so the PATCH is accepted and changes nothing; the document is still `Draft`.

```console
$ curl -s -X PATCH http://localhost:8131/api/documents/2/ -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"status":"Approved"}'
HTTP 200
{
  "id": 2,
  "lease_id": 2,
  "name": "cypress-ave-lease.pdf",
  "file": null,
  "version": 1,
  "uploaded_at": "2026-11-07T21:49:14.586060Z",
  "status": "Draft",
  "gpt_response": {
    "status": "Draft",
    "message": "A twelve-month fixed term at $2,450 a month with a two-month security deposit. Three clauses are worth negotiating before signing: the blanket subletting ban, an uncapped late fee, and a repair responsibility that is pushed onto the tenant below $500.",
    "created_time": "2026-11-07T21:49:14.581323+00:00"
  },
  "analysis": {
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
    "created_time": "2026-11-07T21:49:14.581323+00:00"
  },
  "chat_history": [
    {
      "role": "user",
      "content": "Can I sublet on Airbnb?",
      "timestamp": "2026-11-07T21:49:32.181435+00:00"
    },
    {
      "role": "assistant",
      "content": "The subletting clause is the one to push on. As written it bans assignment and subletting outright, with no consent mechanism, which rules out the short-term-rental use you are underwriting. The usual counter is to accept the ban on assignment but ask for subletting \"with landlord consent, not to be unreasonably withheld\", and to name the platform you intend to list on so the landlord is not surprised later.",
      "timestamp": "2026-11-07T21:49:32.181461+00:00"
    }
  ]
}
```

## 10. Regulations: the curated source answers inside the request

```console
$ curl -s -X POST http://localhost:8131/api/regulations/ -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"search":"santa monica ca"}'
HTTP 201
{
  "id": 5,
  "date": "2026-11-07",
  "search": "santa monica ca",
  "status": "STR Not Allowed",
  "gpt_response": {
    "status": "STR Not Allowed",
    "message": "Only home-sharing is legal; unhosted short-term rental is not.\n\n- The host must live on the property and remain present during the stay.\n- A home-sharing licence and transient occupancy tax registration are required.\n- Vacation rentals - the whole unit, no host present, under 30 days - are prohibited outright.\n- Listings must show the licence number, and platforms are liable for unlicensed listings.",
    "created_time": "2026-11-07T21:49:32.341454+00:00",
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

## 11. Regulations: anything else is queued, not waited on

A `202` and a row to poll. The worker log shows the task finishing; a moment later the row holds the answer.

```console
$ curl -s -X POST http://localhost:8131/api/regulations/ -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"search":"Porto, Portugal"}'
HTTP 202
{
  "id": 6,
  "date": "2026-11-07",
  "search": "Porto, Portugal",
  "status": "pending",
  "gpt_response": null,
  "chat_history": [],
  "analysis_state": "pending",
  "source": ""
}
```

```console
$ curl -s http://localhost:8131/api/regulations/6/ -H "Authorization: Bearer $TOKEN"
HTTP 200
{
  "id": 6,
  "date": "2026-11-07",
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
    "created_time": "2026-11-07T21:49:32.442706+00:00"
  },
  "chat_history": [],
  "analysis_state": "complete",
  "source": "llm:demo"
}
```

## 12. Re-analysis is queued the same way

The cached answer is dropped and the lookup goes back to the worker. The previous answer stays visible until the new one lands.

```console
$ curl -s -X POST http://localhost:8131/api/regulations/6/reanalyze/ -H "Authorization: Bearer $TOKEN"
HTTP 202
{
  "id": 6,
  "date": "2026-11-07",
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
    "created_time": "2026-11-07T21:49:32.442706+00:00"
  },
  "chat_history": [],
  "analysis_state": "pending",
  "source": "llm:demo"
}
```

## 13. Ownership is enforced, not assumed

No token is a `401`. Another user's row is a `404`, so the response does not confirm the id exists.

```console
$ curl -s http://localhost:8131/api/regulations/6/
HTTP 401
{
  "detail": "Authentication credentials were not provided."
}
```

```console
$ curl -s http://localhost:8131/api/regulations/6/ -H "Authorization: Bearer $RIVAL_TOKEN"
HTTP 404
{
  "detail": "No Regulations matches the given query."
}
```

## 14. Password reset does not reveal accounts

An unknown address gets the same `200` as a known one, and no mail is sent.

```console
$ curl -s -X POST http://localhost:8131/account/password/reset/ -H 'Content-Type: application/json' -d '{"email":"nobody@bnbu.test"}'
HTTP 200
{
  "message": "If that address has an account, a reset link is on its way."
}
```

## 15. Creating a lease through the list route

The owner is the caller. This route used to fail with a `500` because nothing set it.

```console
$ curl -s -X POST http://localhost:8131/api/leases/ -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"address1":"9 Elm St","city":"Austin","state":"TX","zip_code":"78701"}'
HTTP 201
{
  "id": 4,
  "date": "2026-11-07",
  "address1": "9 Elm St",
  "address2": null,
  "city": "Austin",
  "state": "TX",
  "zip_code": "78701",
  "status": "Draft",
  "num_of_docs": 0,
  "documents": []
}
```

## 16. The test suite

`python manage.py test` runs 250 tests against PostgreSQL or SQLite with no network and no
API key; see the README's "Without Docker" section for the exact commands.
