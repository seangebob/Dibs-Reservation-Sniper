# Dibs, a sniper for reservations.
*reserves different restaurants + recreational places in KW area by just one prompt*

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
└── orchestrator/
    ├── engine.py              # Extraction → validation coordination
    ├── schemas.py             # Strict provider and public API contracts
    ├── validator.py           # Deterministic completeness and routing rules
    └── providers.py           # OpenAI structured-output adapter
```

The LLM only extracts untrusted text into `ReservationExtraction`. Application code then independently validates required values, rejects past dates and invalid time windows, fixes the market to `Kitchener-Waterloo, ON`, and selects one route:

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
- `WATCH_REQUIRED`, explicitly deferred until Redis/persistence in Milestone 3

```json
{
  "prompt": "Book Cote for four next Saturday at 7 pm"
}
```

A `MOCK_BOOKED` response includes the validated `intent`, considered `slots`, and a `booking` whose provider is `mock` and whose status is explicitly `MOCK_CONFIRMED`. Repeating a semantically identical booking request returns the original confirmation even if the service object is recreated over the same adapter. This endpoint never contacts OpenTable, Resy, a venue, or any other booking provider.

Run deterministic tests without model or booking-provider API calls:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

The provider uses OpenAI's Responses API with native Pydantic structured output, following the [official Structured Outputs guide](https://platform.openai.com/docs/guides/structured-outputs). Content from that guide was rephrased for compliance with licensing restrictions.

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
Milestone 1: Natural Language Parser (Probably most difficult)
Prove LLM can translate into plain english

```python
from pydantic import BaseModel, Field
from typing import Optional

class ReservationIntent(BaseModel):
    restaurant: str = Field(description="Name of the restaurant or café")
    party_size: int = Field(description="Number of guests")
    date: str = Field(description="Target date in YYYY-MM-DD format")
    preferred_time: str = Field(description="Target time, e.g., 19:00")
    missing_info: Optional[str] = Field(description="Clarifying question if key details are missing")
```
1. Set up Mini Python / Fast API (or node.js with vercel)
2. define target schema (from above)
3. Connect OpenAI/Claude via `instructor` or native Structure outputs.
    - have test inputs and verify the output yields clean.
