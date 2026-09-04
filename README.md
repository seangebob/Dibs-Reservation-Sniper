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

## Milestone 3: Distributed watch coordinator

Milestone 3 turns `CREATE_WATCH` from a deferred stub into a coordinated
background job: persisted, fenced against duplicate concurrent delivery,
polled on a jittered interval, recovered after a crash or restart, and
finished the moment a slot appears or its lifetime runs out.

```text
backend/
├── api/
│   ├── dependencies.py        # Shared FastAPI dependency wiring
│   └── routes/
│       └── watches.py         # POST/GET/DELETE /api/watches
├── db/
│   ├── database.py            # Redis connection factory
│   └── repositories/
│       ├── watches.py         # WatchRepository: atomic state machine, in-memory + Redis-Lua
│       ├── watch_scripts.py   # The exact Redis Lua source
│       ├── watch_decisions.py # Typed decision results shared by both stores
│       └── mock_booking.py    # Shared mock provider state (in-memory + Redis-Lua)
├── models/
│   ├── watch.py               # Watch, WatchStatus, poll results (public contract)
│   └── watch_runtime.py       # WatchRuntime sidecar: fencing, cadence, retention (internal)
├── orchestrator/
│   └── router.py              # Sends ready intents to booking or watch
├── services/
│   ├── watch_service.py       # Claim-first poll state machine
│   ├── watch_policy.py        # Derives each watch's attempt budget from its lifetime
│   ├── watch_recovery.py      # RecoveryCoordinator: leader lease + reconciliation
│   ├── readiness.py           # ReadinessTracker: evidence-based /health fields
│   └── notification_service.py
└── workers/
    ├── celery_app.py          # Celery bound to the Redis broker
    ├── scheduler.py           # Jittered poll pacing
    ├── queue.py                # TaskQueue dispatch boundary
    ├── dispatcher.py          # WatchScheduleDispatcher: single-flight due-marker publishing
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

**Redis is an upgrade, not a requirement.** The app boots with an in-memory
repository and an in-process asyncio queue, so `uvicorn backend.main:app` works
with no infrastructure at all. That fallback is not restart-durable — pending
polls and recovery state are lost on process exit, and coordination is only
within one process — which is what the Redis-backed atomic repository and the
Celery worker are for. `GET /health` reports which store and queue are
actually live; see [Health and readiness](#health-and-readiness) below.

Redis Cluster and Sentinel are not supported. Startup checks `cluster_enabled`
on connect, and if cluster mode is detected the app logs a warning, stays on
the in-memory fallback, and never runs the atomic Lua scripts against a
sharded keyspace (they assume every key for one watch lives on one node). A
standalone server, a directly addressed primary, and `rediss://` all work.

See the loop without any infrastructure:

```bash
PYTHONPATH=. python3 scripts/watch_demo.py
```

### How the polling works

`WatchService.poll_window(watch_id, window_id)` is the window-aware queue
handler; `poll_once(watch_id)` is a compatibility wrapper that resolves the
current window itself, for a delivery that only carries the watch id. Neither
imports Celery or Redis directly — the Celery task is a thin wrapper, which is
what lets the whole contract be tested without a broker.

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
- A **provider outage does not kill the watch and does not consume an attempt**.
  The error is recorded in `last_error`, an exponential backoff (capped by
  `WATCH_PROVIDER_BACKOFF_MAX_SECONDS`) paces the retry, and polling continues —
  the outage is temporary and the reservation the user wanted is not.
- Auto-book uses the **watch itself as the idempotency key**
  (`watch:{watch_id}`), so a job the broker redelivers replays the same
  reservation instead of making a second one. This idempotency guarantee is
  only as strong as the adapter behind it: the built-in mock provider is
  authoritative, but a real provider integration is not assumed to offer the
  same guarantee unless it implements the tri-state reconciliation contract
  (`CONFIRMED` / `DEFINITIVELY_ABSENT` / `UNKNOWN`) in `ReservationAdapter`.
- **Cancelling** is a status change, not a dequeue. A cancellation that arrives
  before a booking call commits `CANCELLED` immediately; one that arrives after
  a booking permit was already granted resolves to whichever the provider
  actually did — a real `BOOKED` watch is never overwritten by a cancellation
  that lost the race.

### Coordination and delivery safety

Two independent concerns make polling safe under duplicate delivery, crashes,
and multiple replicas:

- **Fenced single-flight claims.** Every cadence window has a monotonic fence
  token. A poll must hold the current, unexpired claim to commit a result;
  a stale or superseded delivery is rejected rather than silently overwriting
  newer state. This lives in `WatchRepository`'s atomic protocol
  (`claim_window` / `begin_booking` / `commit_window`), implemented identically
  in memory and as exact Redis Lua scripts (`watch_scripts.py`), so both stores
  make the same fencing decision from the same inputs.
- **Startup and follow-up recovery.** `RecoveryCoordinator` reconciles the
  active watch index after every process start and on a bounded interval
  thereafter: pruning stale index entries, expiring exhausted watches,
  re-publishing due schedule markers, and repairing a legacy or crash-orphaned
  record that lost its marker. In Redis mode, only the replica holding the
  finite `dibs:recovery:leader` lease scans; per-window dispatch leases remain
  the final idempotency boundary even if two replicas briefly overlap. In
  memory mode there is nothing to coordinate across processes, so recovery is
  process-local and makes no restart-durability claim.

### Shared mock provider state

The built-in mock booking provider (`MockBookingAdapter`) is stateless — every
adapter instance in the process delegates to one injected
`MockBookingStateRepository`, so the API and every worker child observe the
same slots, bookings, and idempotency records instead of diverging per-process
dictionaries. It is bounded, not unlimited: capacity, idle eviction, and
booking-record retention are all configured (see the settings table below),
and Redis mode implements the identical publish/admit/evict/pin/reconcile
model as exact Lua so the two backends agree on every eviction and replay
decision.

### Terminal retention and cleanup

A finished watch (`FOUND`, `BOOKED`, `EXPIRED`, or `CANCELLED`) stays fully
readable through `WATCH_TERMINAL_RETENTION_SECONDS` (a week by default), then
a bounded cleanup pass — run as part of every recovery sweep — removes its
document, sidecar, and index membership. Native Redis key TTLs are only a
crash backstop; because Redis cannot atomically remove a set member when a key
expires by TTL alone, every read/list/cleanup/recovery path self-heals a
missing or corrupt index member on sight rather than returning it as valid.

### Health and readiness

`GET /health` always returns `200` with `status: "ok"`; the additive fields
below expose degradation without changing that top-level meaning or the
pre-existing `service`, `config`, and `watch_store` fields:

```json
{
  "status": "ok",
  "service": "dibs-mvp",
  "config": "ok",
  "watch_store": "redis",
  "watch_queue": "celery",
  "queue_readiness": "ready",
  "recovery_readiness": "ready"
}
```

- `watch_queue` is the actually bound queue (`asyncio` or `celery`), never
  merely what was configured.
- `queue_readiness` is `ready` for an open asyncio queue on the running loop,
  or after a successful finite Celery broker dispatch; `degraded` after a
  performed dispatch failure; `unknown` before any dispatch has been
  attempted. A Redis ping alone does not prove a Celery worker is consuming.
- `recovery_readiness` is `ready` after a complete reconciliation pass with no
  due backlog; `degraded` after a failed candidate, a due backlog, or a lost
  leader lease; `unknown` before this process has ever led a reconciliation
  pass (another healthy replica may be leading instead).

### Watch API

```text
POST   /api/watches?auto_book=false   # 201, durably scheduled + best-effort dispatched
GET    /api/watches?active_only=false
GET    /api/watches/{watch_id}
DELETE /api/watches/{watch_id}        # cancels; the queued poll stops itself
```

`POST /api/parse-and-book` now routes a ready `CREATE_WATCH` intent here
automatically and returns `WATCH_CREATED` with the `watch_id` to poll.

Every creation response also carries `X-Watch-Monitoring-Policy`
(`deadline` or `attempt-limited`) and `X-Watch-Max-Availability-Checks`. Most
watches are **deadline-capable**: `WATCH_MAX_POLL_ATTEMPTS` comfortably covers
every check the watch could need before its reservation date, so it is really
watching until the date arrives. A watch whose derived requirement exceeds
that configured ceiling is **attempt-limited** instead — it also carries a
`Warning` header, so a client that surfaces standard HTTP headers tells the
user monitoring may stop before the reservation date.

State still lives in Redis rather than PostgreSQL; durable user-owned records
arrive with Milestone 4, and `WatchRepository` is the seam that swap goes
through.

### Configuration

All watch/worker settings are read once at startup by `WatchSettings`/`Settings`
and validated with closed bounds — an out-of-range or malformed value fails
startup with a `ConfigurationError` rather than silently clamping:

| Variable | Default | Bounds |
| --- | --- | --- |
| `RESERVATION_TIMEZONE` | `America/Toronto` | any IANA zone |
| `REDIS_URL` | `redis://localhost:6379/0` | `redis://`, `rediss://`, or `unix://` |
| `WATCH_POLL_INTERVAL_SECONDS` | 180 | 15–3,600 |
| `WATCH_POLL_JITTER_SECONDS` | 30 | 0 ≤ jitter < interval |
| `WATCH_MAX_POLL_ATTEMPTS` | 25,000 | 1–1,000,000 |
| `WATCH_DISPATCH_HORIZON_SECONDS` | 300 | 30–3,600 |
| `WATCH_PROVIDER_CALL_TIMEOUT_SECONDS` | 45 | 1–45 |
| `WATCH_PROVIDER_BACKOFF_MAX_SECONDS` | 3,600 | ≥ interval, ≤ 86,400 |
| `WATCH_TERMINAL_RETENTION_SECONDS` | 604,800 | 3,600–31,536,000 |
| `WATCH_RECOVERY_LEADER_LEASE_SECONDS` | 30 | 5–300 |
| `WATCH_RECOVERY_SWEEP_SECONDS` | 30 | 5–3,600 |
| `MOCK_SLOT_CAPACITY` | 10,000 | 1–100,000 |
| `MOCK_SLOT_IDLE_TTL_SECONDS` | 3,600 | 60–604,800 |
| `MOCK_BOOKING_RETENTION_SECONDS` | 604,800 | 604,800–31,536,000 |

`WATCH_DISPATCH_HORIZON_SECONDS` is how far ahead a due schedule marker is
handed to Celery — far-future work stays durable in Redis rather than becoming
a multi-day broker ETA. `WATCH_PROVIDER_CALL_TIMEOUT_SECONDS` bounds one
provider-sequence attempt and must leave headroom under Celery's 60-second
soft time limit once commit work is added.

### Deploying a change

Because old (unfenced) and new (fenced) worker code are not safe to run
concurrently against the same Redis, a rolling deploy is not supported for
this component. Deploy in this order:

1. **Drain and stop old workers first** — let in-flight polls finish, then
   stop consuming.
2. **Deploy the API and worker from the same image/version.** Mixed versions
   can commit or fence state inconsistently.
3. **Let startup recovery run** before workers start consuming again —
   `RecoveryCoordinator`'s initial pass reconciles the active index and
   republishes anything due, so no watch is stuck on the old topology.
4. **Start consumers** once `queue_readiness`/`recovery_readiness` on
   `/health` report `ready` (or a bounded time has passed and only `unknown`
   remains, for a fresh deployment with no prior observations).

Rollback follows the same order: stop workers first, then roll back the
image, then let recovery run again before workers resume.

## Milestone 4: Write API + browser frontend

Milestone 4 adds a Next.js frontend (`frontend/`) and the seams it needs: a
durable, owner-scoped watch-history projection in PostgreSQL, browser CORS, and
an anonymous client identity. At this milestone there was **no authentication** —
a visitor's browser generates an opaque `X-Dibs-Client-Id` token, persists it in
`localStorage`, and sends it so the API can answer "which watches are mine."

> Milestone 5 added real accounts on top of this seam. The anonymous identity
> above still works exactly as described — it is now the logged-out path, and a
> visitor's anonymous watches are claimed by their account on signup/login.

### New configuration

| Variable | Where | Purpose |
| --- | --- | --- |
| `POSTGRES_URL` | backend | Enables the durable watch-history projection. **Optional** — unset, the backend runs exactly as before and `/api/watches/mine` returns `[]`. A set-but-malformed value fails startup with `ConfigurationError`. |
| `FRONTEND_ORIGINS` | backend | Comma-separated browser origins allowed to call the API cross-origin (e.g. `http://localhost:3000`). **Optional** — unset, zero CORS headers are sent (byte-identical to Milestone 3). A browser frontend on another origin cannot call the API until this is set. |
| `NEXT_PUBLIC_API_BASE_URL` | frontend | Base URL of the backend, **inlined into the client bundle at build time**. The browser calls the API directly, so it must be reachable from the visitor's machine (`http://localhost:8000`), never a docker-internal host. Defaults to `http://localhost:8000`. |

**Migrations run at startup.** When `POSTGRES_URL` is set and reachable, the
backend applies any pending ordered `.sql` migrations (tracked in a
`schema_migrations` table) once during startup, before serving requests. A
PostgreSQL outage never blocks a watch operation — history recording is a
passive observer, and its last outcome is reported additively on `/health` as
`history_readiness`.

### Running frontend + backend together (PowerShell)

Each of these is a long-running process — run them in **separate** terminals and
leave them open.

```powershell
# 1. Infrastructure (Redis + PostgreSQL), detached:
docker compose -f infra/docker-compose.yml up -d

# 2. Backend API (leave running):
$env:OPENAI_API_KEY = "your-api-key"
$env:POSTGRES_URL = "postgresql://dibs:dibs@localhost:5432/dibs"
$env:FRONTEND_ORIGINS = "http://localhost:3000"
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload

# 3. Frontend (separate terminal, leave running):
cd frontend
npm install            # first run only
npm run dev            # http://localhost:3000
```

### Optional: the whole stack in Docker

The `app` compose profile adds `api`, `worker`, and `web` (Next.js) on top of
Redis and PostgreSQL. The default no-profile `up` still starts **only** Redis +
PostgreSQL:

```powershell
# Pass your OpenAI key through to the api container, then bring up all services:
$env:OPENAI_API_KEY = "your-api-key"
docker compose -f infra/docker-compose.yml --profile app up --build
# web → http://localhost:3000   api → http://localhost:8000
```

The api/worker containers reach PostgreSQL over the compose network; the `web`
image bakes `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000` so the browser
reaches the api's host-published port. Set `OPENAI_API_KEY` in the shell before
`up` (compose passes it through) — without it the service still boots, but
prompt parsing returns 503.

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

## Milestone 5: Accounts and authentication

Milestone 5 turns Milestone 4's anonymous `X-Dibs-Client-Id` scoping into a real
access boundary. Accounts are email + password (argon2id); a session is an opaque
bearer token whose `sha256` is all the server stores, sent as
`Authorization: Bearer <token>`.

The guiding rule is that **authentication is an optional lens, never a gate**: a
request with no `Authorization` header behaves byte-for-byte as it did in
Milestone 4, and with no PostgreSQL the account endpoints report a clear
"accounts unavailable" 503 rather than crashing.

| Endpoint | Purpose |
| --- | --- |
| `POST /api/auth/signup` | Create an account, open a session, claim this browser's anonymous watches |
| `POST /api/auth/login` | Open a session, claim this browser's anonymous watches |
| `POST /api/auth/logout` | Revoke the presented session (idempotent) |
| `GET /api/auth/me` | The signed-in account: `id`, `email`, `created_at` |

Ownership lives entirely in the `watch_history` projection (`user_id`), never on
the public `Watch` model. `GET /api/watches/mine` scopes by account when
authenticated; `GET`/`DELETE /api/watches/{id}` answer `404` for an account-owned
watch requested by anyone else — indistinguishable from "not found", so the
boundary leaks no existence. Anonymous watches stay reachable by id exactly as in
Milestones 1–4.

| Variable | Default | Purpose |
| --- | --- | --- |
| `SESSION_TTL_SECONDS` | `2592000` | Session lifetime (30 days) |
| `PASSWORD_MIN_LENGTH` | `8` | Signup password policy |
| `LOGIN_THROTTLE_MAX_ATTEMPTS` | `10` | Failed logins per window before `429` |
| `LOGIN_THROTTLE_WINDOW_SECONDS` | `300` | That window |

## Milestone 6: Notification delivery

Until Milestone 6, Dibs could watch, book, and durably record a reservation but
could not tell anyone: the notifier wrote a log line. A watch that found a table
at 2am notified nobody. This milestone makes the announcement real, which is the
payoff of Milestone 5 — before accounts there was no address to send to.

Delivery is **best-effort and never retried**. A failure is logged and abandoned
rather than queued, so nobody is ever emailed twice about the same event, and the
dashboard remains the durable record. Notification is isolated from watch state:
it runs *after* the history write, inside a timeout, and its failures can never
change a committed transition or fail a poll.

Both the API process and the Celery worker compose the same projection and
notifier, so a background poll behaves exactly like an in-process one.

Set `SMTP_HOST` to enable email; leave it unset and everything behaves exactly as
Milestone 5 did, with log-only notifications.

| Variable | Default | Purpose |
| --- | --- | --- |
| `SMTP_HOST` | unset | Relay host. Unset ⇒ email disabled |
| `SMTP_PORT` | `587` | Relay port |
| `SMTP_FROM` | — | Sender address; **required** once `SMTP_HOST` is set |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | unset | Both, or neither for an open relay |
| `SMTP_STARTTLS` | `true` | Negotiate TLS |
| `SMTP_TIMEOUT_SECONDS` | `10` | Socket timeout, and the notification ceiling |
| `DASHBOARD_BASE_URL` | `http://localhost:3000` | Base for the link in a message |
| `PROMPT_THROTTLE_MAX_REQUESTS` | `20` | Requests per window to the paid prompt endpoints |
| `PROMPT_THROTTLE_WINDOW_SECONDS` | `300` | That window |

The notification log line carries only the watch id, event, and attempt count —
the venue, date, and party size were removed, because they are someone's
reservation and a log aggregator is the wrong place for them.

for this week:
# Milestone 3: Set Up Background Queue + State
- Set up event-driven task queue instead

1. Start local Redis instance via Docker (`docker run -p 6739:6739 redis`)
2. Implement Celery (Python) or BullMQ (Node.js or TypeScript) for background jobs
3. Write simpler queue handler that accepts a watch request and executes polling check every few minutes with a randomized jitter (around 30 secs) to emulate human behaviour

HAVE THIS DONE 9/3/26
