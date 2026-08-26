# Dibs, a Reservation Sniper.
*reserves different restaurants + recreational places in KW.*

Below is the high-level system architecture outlining how user requests flow from the front-end website through the AI orchestration engine and down to execution or clarification paths.

```
┌─────────────────────────────────────────────────────────────┐
│                      User via Website                       │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│             API Gateway (Auth, Rate Limiting)               │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                  AI Orchestration Engine                    │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                    Booking Reservations                     │
└──────────────┬───────────────────────────────┬──────────────┘
               │                               │
       [ Function Call ]               [ Missing Info ]
               │                               │
               ▼                               ▼
┌──────────────────────────────┐┌──────────────────────────────┐
│  Booking, Search, Database   ││ Clarify details with user    │
│            Writes            │└──────────────────────────────┘
└──────────────┬───────────────┘
               │
               ▼
┌────────────────────────────────┐
│  Background Workers / Polling  │
└────────────────────────────────┘

```

--- 

System Design Breakdown:
1. Client & Presentation Layer
    - Captures unstructured input from user (text-to-speech, raggy text) and renders system status updates
        - key design: keep business logic out of control on front end. sends raw strings to backend (e.g., "Set a watch for 4 people at Cote next Saturday night") and receives a structured and user-friendly status back.

2. Gate & Security Layer
    - manages authentication, rate limiting, and input sanitization
        - prevents API abuse and ensuring malicuious user input (prompt injections trying to override system) that can be intercepted before touching billing quotas or databases.

3. AI Orchestration Engine
    - structures regex parsers or NLP tokenizers with LLM using structured JSON mode / outputs

    Execution flow:
    - user's prompt is combined with system guidelines and relevant session context
    - LLM evaluates prompt against defined functions
    - model returns strict, machine-readable JSON rather than free-form text

```json
{
  "status": "READY",
  "route": "WATCH_SERVICE",
  "action": "CREATE_WATCH",
  "venue_name": "Cote",
  "venue_type": "RESTAURANT",
  "party_size": 4,
  "date": "2026-08-22",
  "preferred_time": null,
  "time_window": {"start": "18:00", "end": "21:00"}
}
```
    Guardrails and fallback
    - if mandatory parameters are missing (e.g., did not mention paraity size), the engine catches validation failure and asks targetted follow-up question instead of of calling downstream servicesz

4. Execution + Business Logic Layer
    Role:
        - Takes validated JSON parameters from AI Orchestrator and executes real application logic
    Synchronous Actions:
        - Immediate tasks (querying current database records or authenticating a user) runs directly via backend services
    Asynchronus Actions:
        - Long-running tasks (e.g, 24/7 resevation, monitoring, sending emails, or batch scraping) are pushed to background task queue (e.g, Redis + Celery / BullMQ (wgat))


5. Persistence Layer
    - Database (PostgreSQL / MongoDB): Stores user profiles, past conversation logs, structured entity requests, and system stakes
    - Redis Cache & Queue, Manages background job scheduling with backoff/jitter strategies to execute tasks smoothly without getting rate-limited by third-party services

---

## Milestone 1 MVP: AI orchestrator

The MVP is implemented under `backend/orchestrator` and deliberately stops before booking execution:

```text
backend/
├── main.py                    # FastAPI router and provider lifecycle
├── config.py                  # Environment configuration
├── data/
│   └── venues.py              # Mock KW venue catalog and name resolution
└── orchestrator/
    ├── engine.py              # Extraction → validation coordination
    ├── schemas.py             # Strict provider and public API contracts
    ├── validator.py           # Deterministic completeness and routing rules
    └── providers.py           # OpenAI structured-output adapter
```

The LLM only extracts untrusted text into `ReservationExtraction`. Application code then independently validates required values, fixes the market to `Kitchener-Waterloo, ON`, and selects one route. Deterministic checks the model cannot override:

- past dates, impossible dates such as `2026-02-30`, and dates more than a year out are refused
- a time that has already passed today is refused, and a window that started earlier today is clamped to the next fifteen-minute boundary
- invalid time windows are dropped, and a preferred time outside its own window is queried
- a venue name matching several catalog venues is asked about instead of guessed; a unique match is canonicalized and typed from the catalog

Routes:

- `BOOKING_SERVICE` for ready booking or availability requests
- `WATCH_SERVICE` for ready monitoring requests
- `CLARIFICATION` when required details are missing or invalid

No booking, search, database write, or background job is executed in Milestone 1.

### Setup (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
$env:OPENAI_API_KEY = "your-api-key"
$env:OPENAI_MODEL = "gpt-5.6"
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload
```

Run the server command manually because it remains active until stopped. Configuration variables are listed in `.env.example`; secrets are not committed or sent anywhere except the configured OpenAI API.

### API contract

`POST /api/orchestrator/parse`

```json
{
  "prompt": "Set a watch for two people at Grand River Rocks next Saturday between 6 and 9 pm for two hours"
}
```

Ready response:

```json
{
  "status": "READY",
  "route": "WATCH_SERVICE",
  "action": "CREATE_WATCH",
  "venue_name": "Grand River Rocks",
  "venue_type": "RECREATION",
  "market": "Kitchener-Waterloo, ON",
  "party_size": 2,
  "date": "2026-08-22",
  "preferred_time": null,
  "time_window": {"start": "18:00", "end": "21:00"},
  "duration_minutes": 120,
  "special_requests": [],
  "missing_fields": [],
  "clarification_question": null
}
```

When information is missing, `route` is `CLARIFICATION`, `missing_fields` identifies the gaps, and `clarification_question` contains one targeted follow-up. Downstream services must only consume results whose `status` is `READY`.

## Milestone 2 MVP: Mock platform adapter

Milestone 2 adds a provider-neutral execution layer without contacting a real venue:

```text
backend/
├── integrations/
│   ├── base.py               # Async ReservationAdapter contract
│   └── mock_booking.py       # Deterministic in-memory slots/bookings
├── models/
│   └── reservation.py        # Query, slot, confirmation, and result models
└── services/
    └── booking_service.py    # Search/book business rules
```

`POST /api/parse-and-book` accepts the same raw prompt. After orchestration it can return:

- `CLARIFICATION_REQUIRED` without calling an adapter
- `AVAILABILITY_FOUND` with mock slots and no booking
- `NO_AVAILABILITY`
- `MOCK_BOOKED` with an idempotent mock confirmation
- `WATCH_CREATED` with a `watch_id`, once Milestone 3 wired up the background queue

```json
{
  "prompt": "Book Cote for four next Saturday at 7 pm"
}
```

A `MOCK_BOOKED` response includes the validated `intent`, considered `slots`, and a `booking` whose provider is `mock` and whose status is explicitly `MOCK_CONFIRMED`. Repeating a semantically identical booking request returns the original confirmation even if the service object is recreated over the same adapter, and two identical requests running at once share one confirmation rather than racing. This endpoint never contacts OpenTable, Resy, a venue, or any other booking provider.

Mock slots follow `backend/data/venues.py` so they stay plausible: fifteen-minute
starts inside that venue's hours for that weekday, nothing that would run past
closing, per-slot table sizes so a large party sees fewer options than a couple,
and no availability on holiday closures or sold-out dates. The same request
always produces the same slot identifiers. Venues outside the catalog fall back
to a generic KW profile rather than being rejected.

Holidays are computed, not hard-coded, for every year in `SUPPORTED_YEARS`,
including the movable ones (Easter from the Gregorian computus, Family Day,
Victoria Day, the Civic Holiday, Labour Day, and Thanksgiving Monday). They fall
into three behaviours:

- **Closed** — New Year's Day, Good Friday, Easter Sunday, Thanksgiving Monday,
  Christmas Day, and Boxing Day return no slots at all.
- **Closed for restaurants, open for recreation** — a climbing gym, bowling
  alley, and tube park work Good Friday and the Thanksgiving long weekend.
- **Open but sold out** — Valentine's Day, Mother's Day, Christmas Eve, New
  Year's Eve, and the long-weekend Mondays (Family Day, Victoria Day, Canada
  Day, Civic Holiday, Labour Day) have hours but no free tables.

Run ten realistic prompts end to end, using the real model when `OPENAI_API_KEY`
is set and scripted extractions when it is not:

```powershell
$env:PYTHONPATH = "."
.\.venv\Scripts\python.exe scripts\spot_check.py
```

Run deterministic tests without model or booking-provider API calls:

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m pytest --cov=backend --cov-report=term-missing
```

The provider uses OpenAI's Responses API with native Pydantic structured output, following the [official Structured Outputs guide](https://platform.openai.com/docs/guides/structured-outputs). Content from that guide was rephrased for compliance with licensing restrictions.

## Milestone 3 MVP: Background queue + state

Milestone 3 turns `CREATE_WATCH` from a deferred stub into a real background
job. A watch is persisted, polled on a jittered interval, and finished the
moment a slot appears.

```text
backend/
├── api/
│   ├── dependencies.py        # Shared FastAPI dependency wiring
│   └── routes/
│       └── watches.py         # POST/GET/DELETE /api/watches
├── db/
│   ├── database.py            # Redis connection factory
│   └── repositories/
│       └── watches.py         # WatchRepository + in-memory and Redis stores
├── models/
│   └── watch.py               # Watch, WatchStatus, poll results
├── orchestrator/
│   └── router.py              # Sends ready intents to booking or watch
├── services/
│   ├── watch_service.py       # Watch lifecycle + the poll handler
│   └── notification_service.py
└── workers/
    ├── celery_app.py          # Celery bound to the Redis broker
    ├── scheduler.py           # Jittered poll pacing
    ├── queue.py               # TaskQueue dispatch boundary
    └── tasks/
        └── monitor_watch.py   # The Celery task
```

### Running it

Start Redis (note: the port is **6379**, not 6739):

```bash
docker compose -f infra/docker-compose.yml up -d
# or, for Redis alone:
docker run -d -p 6379:6379 redis:7-alpine
```

Then the API, and — for real distributed jobs — a Celery worker:

```bash
pip install -e ".[test,worker]"
uvicorn backend.main:app --reload
celery -A backend.workers.celery_app worker --loglevel=info
```

`GET /health` reports which store is live: `"watch_store": "redis"` once Redis
is reachable, `"memory"` otherwise. **Redis is an upgrade, not a requirement** —
the app boots with an in-memory repository and an in-process asyncio queue, so
`uvicorn backend.main:app` works with no infrastructure at all. That fallback is
not durable: pending polls are lost on restart, which is what the Celery worker
is for.

See the loop without any infrastructure:

```bash
PYTHONPATH=. python3 scripts/watch_demo.py
```

### How the polling works

`WatchService.poll_once(watch_id)` is the queue handler, and it is a plain
coroutine — no Celery or Redis import of its own. The Celery task is a thin
wrapper around it, which is what lets the whole contract be tested without a
broker.

Each poll ends in exactly one of these, so a watch can never fan out into
several concurrent polling chains:

| Outcome | Effect |
| --- | --- |
| `NO_AVAILABILITY` | Attempt recorded, one successor job queued after a jittered delay |
| `FOUND` | Slots stored, watch finished, owner notified |
| `BOOKED` | Slot booked idempotently (auto-book watches only), watch finished |
| `EXPIRED` | Attempts or the reservation date ran out; nothing requeued |
| `ALREADY_FINISHED` | The watch was cancelled or resolved; the chain stops |
| `UNKNOWN_WATCH` | The watch is gone; the chain stops |

Delays are `WATCH_POLL_INTERVAL_SECONDS ± WATCH_POLL_JITTER_SECONDS`, defaulting
to 180s ± 30s. The jitter is the point: a perfectly regular cadence is trivially
identifiable as a bot, and it makes watches created at the same moment stampede
the provider together.

Deterministic behaviours worth knowing:

- The **first** check runs immediately with no jitter — the user just asked, so
  that latency is the one they actually see. Jitter starts on retries.
- A watch **expires at the end of its reservation date**; a table for tonight is
  worthless tomorrow. `WATCH_MAX_POLL_ATTEMPTS` is a second, independent ceiling.
- A **provider outage does not kill the watch**. The error is recorded in
  `last_error` and polling continues, because the outage is temporary and the
  reservation the user wanted is not.
- Auto-book uses the **watch itself as the idempotency key**, so a job the
  broker redelivers replays the same reservation instead of making a second one.
- **Cancelling** is a status change, not a dequeue. The next scheduled poll sees
  a terminal status and stops the chain.

### Watch API

```text
POST   /api/watches?auto_book=false   # 201, dispatches the first check
GET    /api/watches?active_only=false
GET    /api/watches/{watch_id}
DELETE /api/watches/{watch_id}        # cancels; the queued poll stops itself
```

`POST /api/parse-and-book` now routes a ready `CREATE_WATCH` intent here
automatically and returns `WATCH_CREATED` with the `watch_id` to poll.

State still lives in Redis rather than PostgreSQL; durable user-owned records
arrive with Milestone 4, and `WatchRepository` is the seam that swap goes
through.

---

FULL SYSTEM DESIGN FOR DIBS:
dibs/
│
├── apps/
│   │
│   ├── web/                              # Next.js frontend
│   │   ├── app/
│   │   │   ├── page.tsx                  # Main Dibs interface
│   │   │   ├── dashboard/
│   │   │   │   └── page.tsx
│   │   │   ├── watches/
│   │   │   │   └── page.tsx
│   │   │   └── api/
│   │   │       └── ...
│   │   │
│   │   ├── components/
│   │   │   ├── PromptInput.tsx
│   │   │   ├── WatchCard.tsx
│   │   │   ├── BookingStatus.tsx
│   │   │   └── ActivityFeed.tsx
│   │   │
│   │   ├── lib/
│   │   │   └── api.ts
│   │   │
│   │   └── types/
│   │       └── api.ts
│   │
│   └── api/                              # FastAPI backend
│       │
│       ├── main.py                       # API entrypoint
│       │
│       ├── api/
│       │   ├── routes/
│       │   │   ├── auth.py
│       │   │   ├── prompts.py
│       │   │   ├── watches.py
│       │   │   ├── bookings.py
│       │   │   └── health.py
│       │   │
│       │   └── dependencies.py
│       │
│       ├── core/
│       │   ├── config.py
│       │   ├── security.py
│       │   ├── rate_limit.py
│       │   └── logging.py
│       │
│       ├── orchestrator/
│       │   ├── engine.py                 # Main AI orchestration
│       │   ├── prompts.py                # System prompts
│       │   ├── schemas.py                # Structured LLM schemas
│       │   ├── parser.py                 # Regex/NLP preprocessing
│       │   ├── validator.py              # Validate extracted params
│       │   ├── guardrails.py              # Prompt injection checks
│       │   └── router.py                 # Decide which action to execute
│       │
│       ├── services/
│       │   ├── restaurant_service.py
│       │   ├── recreation_service.py
│       │   ├── availability_service.py
│       │   ├── booking_service.py
│       │   └── notification_service.py
│       │
│       ├── integrations/
│       │   ├── opentable.py
│       │   ├── resy.py
│       │   ├── recreation_sites.py
│       │   └── email.py
│       │
│       ├── models/
│       │   ├── user.py
│       │   ├── watch.py
│       │   ├── venue.py
│       │   ├── reservation.py
│       │   └── conversation.py
│       │
│       ├── db/
│       │   ├── database.py
│       │   ├── migrations/
│       │   └── repositories/
│       │       ├── users.py
│       │       ├── watches.py
│       │       ├── reservations.py
│       │       └── conversations.py
│       │
│       └── workers/
│           ├── celery_app.py
│           ├── tasks/
│           │   ├── monitor_watch.py
│           │   ├── check_availability.py
│           │   ├── make_reservation.py
│           │   └── send_notification.py
│           └── scheduler.py
│
├── packages/
│   ├── shared-types/
│   │   └── schemas.ts
│   │
│   └── prompts/
│       └── dibs_system_prompt.txt
│
├── tests/
│   ├── unit/
│   │   ├── test_parser.py
│   │   ├── test_validator.py
│   │   └── test_guardrails.py
│   │
│   ├── integration/
│   │   ├── test_orchestrator.py
│   │   └── test_booking_flow.py
│   │
│   └── e2e/
│       └── test_create_watch.py
│
├── scripts/
│   ├── seed_venues.py
│   └── dev_setup.py
│
├── infra/
│   ├── docker-compose.yml                # PostgreSQL + Redis
│   └── Dockerfile
│
├── .env.example
├── .gitignore
├── README.md
└── docker-compose.yml

for this week:
# Milestone 3: Set Up Background Queue + State
- Set up event-driven task queue instead

1. Start local Redis instance via Docker (`docker run -p 6739:6739 redis`)
2. Implement Celery (Python) or BullMQ (Node.js or TypeScript) for background jobs
3. Write simpler queue handler that accepts a watch request and executes polling check every few minutes with a randomized jitter (around 30 secs) to emulate human behaviour

HAVE THIS DONE 9/3/26
