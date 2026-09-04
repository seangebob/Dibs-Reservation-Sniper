# Architecture and reference

Detailed reference for Dibs. For what the project is and how to run it, start with
the [README](../README.md).

---

## Architecture

```text
backend/
├── main.py                     # App composition: lifespan wiring, /health, prompt endpoints
├── config.py                   # Every setting, bounded and validated at startup
├── logging_config.py
├── api/
│   ├── client_identity.py      # Anonymous X-Dibs-Client-Id handling
│   ├── dependencies.py         # Optional-auth lens: current_user / require_user
│   └── routes/
│       ├── auth.py             # signup · login · logout · me
│       └── watches.py          # create · list · mine · read · cancel
├── orchestrator/
│   ├── providers.py            # OpenAI structured-output adapter
│   ├── engine.py               # Extraction → validation coordination
│   ├── validator.py            # Deterministic completeness and routing rules
│   ├── router.py               # Ready intent → booking or watch
│   └── schemas.py              # Strict provider and public contracts
├── integrations/
│   ├── base.py                 # ReservationAdapter: the provider seam
│   ├── mock_booking.py         # Deterministic simulator (the only adapter today)
│   └── email.py                # SMTP notifier
├── services/
│   ├── watch_service.py        # Claim-first poll state machine
│   ├── watch_recovery.py       # Leader lease + reconciliation sweeps
│   ├── watch_policy.py         # Derives each watch's attempt budget from its lifetime
│   ├── booking_service.py      # Search / book business rules
│   ├── auth_service.py         # Sessions, password verification
│   ├── recipients.py           # Watch → email address resolution
│   ├── throttle.py             # Sliding-window rate limiting
│   ├── notification_service.py # NotificationService protocol + logging default
│   └── readiness.py            # Evidence-based /health fields
├── db/
│   ├── database.py             # Redis connection factory
│   ├── postgres.py             # Pool + ordered migration runner
│   ├── migrations/             # 0001_watch_history · 0002_accounts
│   └── repositories/
│       ├── watches.py          # Atomic watch state machine (in-memory + Redis Lua)
│       ├── watch_scripts.py    # The exact Lua source
│       ├── watch_decisions.py  # Typed decision results shared by both stores
│       ├── watch_history.py    # Durable PostgreSQL projection
│       ├── accounts.py         # Users and sessions
│       └── mock_booking.py     # Shared mock provider state
├── models/                     # Watch, WatchRuntime, reservation, account
└── workers/
    ├── celery_app.py           # Celery bound to the Redis broker
    ├── dispatcher.py           # Single-flight due-marker publishing
    ├── scheduler.py            # Jittered poll pacing
    ├── queue.py                # TaskQueue dispatch boundary
    └── tasks/monitor_watch.py  # The Celery task (a thin wrapper)

frontend/       Next.js 15 · React 19 · TypeScript strict · vitest
tests/          55 files, ~15k lines
docs/specs/     Requirements / design / tasks packages per milestone
infra/          docker-compose.yml
misc/scripts/   spot_check.py · watch_demo.py
```

Two rules explain most of the design.

**Degrade, never gate.** Every external dependency is optional, and its absence is a
defined mode rather than an error. No `REDIS_URL` → in-memory store. No
`POSTGRES_URL` → no projection, and `/api/watches/mine` returns `[]`. No `SMTP_HOST` →
notifications log instead of send. No `Authorization` header → the anonymous path,
byte-identical to how it behaved before accounts existed. A *malformed* value still
fails startup loudly; only an *absent* one degrades.

**Seams are protocols, and they have two implementations.** `WatchRepository` is
in-memory and Redis-Lua, held to identical behaviour by equivalence tests.
`ReservationAdapter` is the mock plus a second conformance adapter. Anything with one
implementation and no second is a claim, not a contract.

## The watch engine

`WatchService.poll_window(watch_id, window_id)` is the window-aware queue handler;
`poll_once(watch_id)` resolves the current window itself, for a delivery carrying only
an id. Neither imports Celery or Redis — the Celery task is a thin wrapper, which is
what lets the entire contract be tested without a broker.

Each poll ends in exactly one outcome, so a watch can never fan out into several
concurrent polling chains:

| Outcome | Effect |
| --- | --- |
| `NO_AVAILABILITY` | Attempt recorded, one successor queued after a jittered delay |
| `FOUND` | Slots stored, watch finished, owner notified |
| `BOOKED` | Slot booked idempotently (auto-book only), watch finished |
| `EXPIRED` | Attempts or the reservation date ran out; nothing requeued |
| `ALREADY_FINISHED` | Cancelled or already resolved; the chain stops |
| `UNKNOWN_WATCH` | The watch is gone; the chain stops |

Delays are `WATCH_POLL_INTERVAL_SECONDS ± WATCH_POLL_JITTER_SECONDS`, 180s ± 30s by
default. The jitter is the point: a perfectly regular cadence is trivially
identifiable as a bot, and it makes watches created in the same moment stampede a
provider together.

Behaviours worth knowing:

- **The first check runs immediately, with no jitter.** The user just asked; that
  latency is the one they actually feel. Jitter starts on retries.
- **A watch expires at the end of its reservation date.** A table for tonight is
  worthless tomorrow. `WATCH_MAX_POLL_ATTEMPTS` is a second, independent ceiling.
- **A provider outage does not kill the watch and does not consume an attempt.** The
  error lands in `last_error`, an exponential backoff paces the retry, and polling
  continues — the outage is temporary; the reservation the user wanted is not.
- **Auto-book uses the watch itself as the idempotency key** (`watch:{watch_id}`), so
  a redelivered job replays one reservation instead of making a second. That guarantee
  is only as strong as the adapter beneath it: the mock is authoritative, and a real
  integration is not assumed to be unless it implements the tri-state reconciliation
  contract (`CONFIRMED` / `DEFINITIVELY_ABSENT` / `UNKNOWN`).
- **Cancelling is a status change, not a dequeue.** A cancellation arriving before a
  booking commits wins immediately; one arriving after a booking permit was granted
  resolves to whatever the provider actually did. A real `BOOKED` watch is never
  overwritten by a cancellation that lost the race.

### Coordination and delivery safety

- **Fenced single-flight claims.** Every cadence window carries a monotonic fence
  token. A poll must hold the current, unexpired claim to commit a result; a stale or
  superseded delivery is rejected rather than silently overwriting newer state. This is
  `WatchRepository`'s atomic protocol — `claim_window` / `begin_booking` /
  `commit_window` — implemented identically in memory and as exact Redis Lua, so both
  stores reach the same fencing decision from the same inputs.
- **Startup and follow-up recovery.** `RecoveryCoordinator` reconciles the active index
  on every process start and on a bounded interval thereafter: pruning stale members,
  expiring exhausted watches, republishing due markers, and repairing crash-orphaned
  records. In Redis mode only the replica holding the finite `dibs:recovery:leader`
  lease scans, and per-window dispatch leases remain the final idempotency boundary
  even if two replicas briefly overlap. In memory mode there is nothing to coordinate
  across processes, and no restart-durability is claimed.
- **Terminal retention.** A finished watch stays fully readable for
  `WATCH_TERMINAL_RETENTION_SECONDS` (a week by default), then a bounded cleanup pass
  inside each recovery sweep removes its document, sidecar, and index membership. Redis
  key TTLs are only a crash backstop — because Redis cannot atomically drop a set
  member when a key expires, every read, list, cleanup, and recovery path self-heals a
  missing or corrupt index member on sight rather than returning it as valid.

### The mock provider

`MockBookingAdapter` is stateless; every instance delegates to one injected
`MockBookingStateRepository`, so the API and every worker child observe the same slots,
bookings, and idempotency records rather than diverging per-process dictionaries. It is
bounded — capacity, idle eviction, and booking retention are all configured — and the
Redis backend implements the identical publish/admit/evict/pin/reconcile model as exact
Lua, so the two agree on every eviction and replay decision.

Slots follow `backend/data/venues.py`: fifteen-minute starts inside that venue's hours
for that weekday, nothing running past closing, per-slot table sizes so a large party
sees fewer options than a couple. The same request always yields the same slot ids.
Venues outside the catalog fall back to a generic KW profile rather than being rejected.

Holidays are **computed, not hard-coded**, for every year in `SUPPORTED_YEARS` —
including the movable ones (Easter from the Gregorian computus, Family Day, Victoria
Day, the Civic Holiday, Labour Day, Thanksgiving Monday) — and fall into three
behaviours:

- **Closed** — New Year's Day, Good Friday, Easter Sunday, Thanksgiving Monday,
  Christmas Day, Boxing Day.
- **Closed for restaurants, open for recreation** — the climbing gym, bowling alley,
  and tube park work Good Friday and the Thanksgiving long weekend.
- **Open but sold out** — Valentine's Day, Mother's Day, Christmas Eve, New Year's Eve,
  and the long-weekend Mondays.

## API

### Prompts

```text
POST /api/orchestrator/parse      # Parse only — returns the validated intent
POST /api/parse-and-book          # Parse, then execute: book, search, or open a watch
```

```json
{ "prompt": "Set a watch for two at Grand River Rocks next Saturday between 6 and 9 pm" }
```

A ready response carries `status`, `route`, `action`, the resolved venue and party
details, `missing_fields`, and `clarification_question`. Downstream consumers must act
only on `status: "READY"`. When details are missing, `route` is `CLARIFICATION` and
exactly one targeted follow-up question comes back.

`/api/parse-and-book` returns one `ExecutionStatus`: `CLARIFICATION_REQUIRED`,
`AVAILABILITY_FOUND`, `NO_AVAILABILITY`, `MOCK_BOOKED`, `WATCH_REQUIRED`, or
`WATCH_CREATED` (with the `watch_id` to poll). Both endpoints are rate limited — they
cost money on every call.

### Watches

```text
POST   /api/watches?auto_book=false    # 201; durably scheduled, best-effort dispatched
GET    /api/watches?active_only=false
GET    /api/watches/mine               # Scoped to this account, or to this browser
GET    /api/watches/{watch_id}
DELETE /api/watches/{watch_id}         # Cancels; the queued poll stops itself
```

Every creation response carries `X-Watch-Monitoring-Policy` (`deadline` or
`attempt-limited`) and `X-Watch-Max-Availability-Checks`. Most watches are
**deadline-capable** — the attempt ceiling comfortably covers every check needed before
the reservation date, so the watch really does run until the date arrives. One whose
derived requirement exceeds the configured ceiling is **attempt-limited** and also
carries a `Warning` header, so a client that surfaces standard headers can tell the user
monitoring may stop early.

### Accounts

```text
POST /api/auth/signup      # Create an account, open a session, claim this browser's watches
POST /api/auth/login       # Open a session, claim this browser's watches
POST /api/auth/logout      # Revoke the presented session (idempotent)
GET  /api/auth/me          # id · email · created_at
```

Passwords are argon2id. A session is an **opaque bearer token whose sha256 is all the
server stores**, sent as `Authorization: Bearer <token>`.

**Authentication is an optional lens, never a gate.** A request with no `Authorization`
header behaves exactly as it did before accounts existed. With no PostgreSQL, the
account endpoints report a clear "accounts unavailable" `503` instead of crashing.

Ownership lives entirely in the `watch_history` projection (`user_id`), never on the
public `Watch` model. `GET`/`DELETE /api/watches/{id}` answer `404` for a watch owned by
another account — indistinguishable from "not found", so the boundary leaks no
existence. Logged out, a browser is identified by an opaque `X-Dibs-Client-Id` it
generates and keeps in `localStorage`; those anonymous watches are claimed by the
account on signup or login. A malformed client id degrades to anonymous rather than
rejecting the request.

### Notifications

A watch that finds a table at 2am has to be able to say so. Delivery is **best-effort
and never retried**: a failure is logged and abandoned rather than queued, so nobody is
ever emailed twice about one event, and the dashboard stays the durable record.
Notification runs *after* the history write, inside a timeout, and its failures can
never change a committed transition or fail a poll. Announcements are gated on a
terminal `event_id` issued at most once per transition, so a redelivered job cannot
announce twice.

The API process and the Celery worker compose the same projection and notifier, so a
background poll behaves exactly like an in-process one. Notification log lines carry
only the watch id, event, and attempt count — the venue, date, and party size are
deliberately omitted, because they are someone's reservation and a log aggregator is
the wrong place for them.

## Configuration

Settings are read once at startup and validated with closed bounds. An out-of-range or
malformed value **fails startup with `ConfigurationError`** rather than silently
clamping. There is no committed `.env` template for the backend — the tables below are
the reference. The frontend ships `frontend/.env.local.example`.

### Core

| Variable | Default | Notes |
| --- | --- | --- |
| `OPENAI_API_KEY` | unset | Required for prompt parsing; without it those endpoints return `503`. |
| `OPENAI_MODEL` | `gpt-4o-mini` | Must be a valid model identifier. |
| `RESERVATION_TIMEZONE` | `America/Toronto` | Any IANA zone. |
| `REDIS_URL` | `redis://localhost:6379/0` | `redis://`, `rediss://`, or `unix://`. Safe to leave unset. |
| `FRONTEND_ORIGINS` | unset | Comma-separated CORS origins. Unset ⇒ zero CORS headers sent. |

### Watch engine

| Variable | Default | Bounds |
| --- | --- | --- |
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

`WATCH_DISPATCH_HORIZON_SECONDS` is how far ahead a due schedule marker is handed to
Celery — far-future work stays durable in Redis instead of becoming a multi-day broker
ETA. `WATCH_PROVIDER_CALL_TIMEOUT_SECONDS` bounds one provider sequence and must leave
headroom under Celery's 60-second soft time limit.

### PostgreSQL projection

| Variable | Default | Notes |
| --- | --- | --- |
| `POSTGRES_URL` | unset | Enables the durable history projection. Unset ⇒ `/api/watches/mine` returns `[]`. |
| `POSTGRES_POOL_MIN_SIZE` / `POSTGRES_POOL_MAX_SIZE` | 1 / 5 | 1–64; max must be ≥ min. |
| `POSTGRES_STATEMENT_TIMEOUT_SECONDS` | 10 | 1–300 |

Ordered `.sql` migrations are applied once at startup under an advisory lock and
tracked in a `schema_migrations` table. A PostgreSQL outage never blocks a watch
operation — history recording is a passive observer, and its last outcome is reported
additively on `/health` as `history_readiness`. Connection failures never disclose the
DSN.

### Accounts and rate limiting

| Variable | Default | Purpose |
| --- | --- | --- |
| `SESSION_TTL_SECONDS` | 2,592,000 | Session lifetime (30 days) |
| `PASSWORD_MIN_LENGTH` / `PASSWORD_MAX_LENGTH` | 8 / — | Signup password policy |
| `LOGIN_THROTTLE_MAX_ATTEMPTS` | 10 | Failed logins per window before `429` |
| `LOGIN_THROTTLE_WINDOW_SECONDS` | 300 | That window |
| `PROMPT_THROTTLE_MAX_REQUESTS` | 20 | Requests per window to the paid prompt endpoints |
| `PROMPT_THROTTLE_WINDOW_SECONDS` | 300 | That window |

### Email

| Variable | Default | Purpose |
| --- | --- | --- |
| `SMTP_HOST` | unset | Relay host. **Unset ⇒ email disabled**, notifications log only. |
| `SMTP_PORT` | 587 | Relay port |
| `SMTP_FROM` | — | Sender address; **required** once `SMTP_HOST` is set |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | unset | Both, or neither for an open relay |
| `SMTP_STARTTLS` | `true` | An unrecognized value is rejected, never treated as false |
| `SMTP_TIMEOUT_SECONDS` | 10 | Socket timeout, and the notification ceiling |
| `DASHBOARD_BASE_URL` | `http://localhost:3000` | Base for the link inside a message |

Once `SMTP_HOST` is set the rest must be coherent — a host with no `SMTP_FROM`, or a
username with no password, is a startup error, because a half-configured mailer that
silently drops every notification is worse than none.

### Frontend

| Variable | Default | Notes |
| --- | --- | --- |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | **Inlined into the bundle at build time.** Must be reachable from the visitor's machine — never a docker-internal host. |

## Health and readiness

`GET /health` always returns `200` with `status: "ok"`. The additive fields expose
degradation without changing that top-level meaning:

```json
{
  "status": "ok",
  "service": "dibs-mvp",
  "config": "ok",
  "watch_store": "redis",
  "watch_queue": "celery",
  "queue_readiness": "ready",
  "recovery_readiness": "ready",
  "history_readiness": "ready"
}
```

- `watch_store` and `watch_queue` report what is **actually bound**, never merely what
  was configured.
- `queue_readiness` — `ready` for an open asyncio queue on the running loop, or after a
  successful finite Celery broker dispatch; `degraded` after a performed dispatch
  failure; `unknown` before any dispatch has been attempted. A Redis ping alone does not
  prove a Celery worker is consuming.
- `recovery_readiness` — `ready` after a complete reconciliation pass with no backlog;
  `degraded` after a failed candidate, a due backlog, or a lost leader lease; `unknown`
  before this process has ever led a pass (another replica may be leading instead).
- `history_readiness` — the outcome of the last projection write.

## Deploying a change

Old (unfenced) and new (fenced) worker code are not safe to run concurrently against
one Redis, so **rolling deploys are not supported** for this component.

1. **Drain and stop old workers first.** Let in-flight polls finish, then stop consuming.
2. **Deploy the API and worker from the same image.** Mixed versions can commit or fence
   state inconsistently.
3. **Let startup recovery run** before workers consume again — the initial pass
   reconciles the active index and republishes anything due, so no watch is stranded on
   the old topology.
4. **Start consumers** once `queue_readiness` and `recovery_readiness` report `ready`
   (or a bounded time has passed with only `unknown` left, on a fresh deployment).

Rollback follows the same order: stop workers, roll back the image, let recovery run
again, then resume.

