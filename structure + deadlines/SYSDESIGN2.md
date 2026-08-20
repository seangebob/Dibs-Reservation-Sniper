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


A first draft for an MVP to be built:

dibs/
├── frontend/                 # Next.js
├── backend/
│   ├── main.py
│   ├── orchestrator/
│   │   ├── engine.py
│   │   ├── schemas.py
│   │   └── validator.py
│   ├── services/
│   │   └── booking_service.py
│   ├── integrations/
│   │   └── mock_booking.py
│   └── models/
│       └── watch.py
│
├── worker/
│   └── monitor.py
│
├── docker-compose.yml        # Redis + PostgreSQL
└── README.md