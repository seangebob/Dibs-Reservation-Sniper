# Design Document

## Overview

Milestone 4 adds two things to a backend that is otherwise unchanged: a Next.js frontend, and a
PostgreSQL-backed durable record of every watch. It deliberately does **not** touch the Redis/
in-memory `WatchRepository` protocol Milestone 3 built — that protocol's entire value is the fenced,
single-flight, leader-elected coordination it took ten tasks to get right, and re-deriving that same
atomicity against PostgreSQL would be a second, riskier implementation of the same problem for no
behavioral gain.

## Key decision: PostgreSQL as a passive projection, not a replacement store

**Decision:** `WatchRepository` (Redis/in-memory) remains the sole source of truth for anything the
Milestone 3 coordination protocol touches — active polling, claims, leases, recovery. A new,
separate `WatchHistoryRepository` (PostgreSQL) stores a denormalized, append-friendly projection of
each watch's current public state, written after the fact whenever `WatchService` produces an
observable outcome. It never participates in a claim, never blocks a poll, and its unavailability
degrades a health field, not correctness.

**Alternatives considered:**

- **Replace `WatchRepository`'s backing store with PostgreSQL entirely.** Rejected: the protocol
  has ~25 methods built around Redis's atomic Lua scripts (`claim_window`, `begin_booking`,
  `commit_window`, leader-lease renewal, dispatch claims). Reimplementing that with equivalent
  atomicity in PostgreSQL means `SELECT ... FOR UPDATE` or advisory locks standing in for
  single-round-trip Lua, a second differential-parity test suite, and re-litigating every race
  condition Milestone 3's 52-test state-machine suite already closed — for a milestone whose actual
  goal is "give it a UI and make records survive a restart," not "migrate the coordination engine."
- **Write the projection synchronously, in the same transaction as the Redis commit.** Rejected:
  there is no cross-store transaction between Redis and PostgreSQL. Making watch creation/polling
  wait on a Postgres write (or roll back a Redis commit if Postgres fails) would let Postgres
  availability become a new failure mode for the exact hot path Milestone 3 spent its effort
  hardening. The projection is intentionally best-effort and asynchronous-safe: Requirement 3.2
  encodes this as "log and continue."
- **Use Postgres LISTEN/NOTIFY or a message queue between the two stores.** Deferred as
  unnecessary complexity: the write volume (one row upsert per poll/state-transition) does not need
  a queue, and every write site already funnels through `WatchService`.

## Architecture

```
apps/
├── web/                              # Next.js — new in this milestone
│   ├── app/
│   │   ├── page.tsx                  # Prompt input (Requirement 1)
│   │   └── watches/page.tsx          # Dashboard (Requirement 4)
│   ├── lib/
│   │   ├── api.ts                    # fetch wrapper, sends X-Dibs-Client-Id
│   │   └── client-id.ts              # generate/read/persist the anonymous id
│   └── types/api.ts                  # mirrors backend/orchestrator/schemas.py by hand
└── api/                              # existing FastAPI backend (this repo's `backend/`)

backend/
├── main.py                           # + CORS middleware, + history repository wiring
├── config.py                         # + PostgresSettings (dsn, pool size)
├── api/
│   ├── dependencies.py               # + get_client_id, + get_history_repository
│   └── routes/
│       └── watches.py                # GET gains ?owner= scoping over the history repo
├── db/
│   ├── postgres.py                   # asyncpg/SQLAlchemy engine + migration runner
│   └── repositories/
│       └── watch_history.py          # WatchHistoryRepository (new)
└── services/
    └── watch_service.py              # unchanged logic; one new call-out per outcome
```

### Data flow for a watch creation

1. Frontend generates/reads the client id, calls `POST /api/parse-and-book` with
   `X-Dibs-Client-Id`.
2. `PromptRouter` → `WatchService.create(...)` runs exactly as in Milestone 3, now additionally
   receiving the caller's client id (threaded through as an optional parameter, defaulting to
   `None` for every existing non-frontend caller — Requirement 2.4).
3. `WatchService` returns as today, then calls `WatchHistoryRepository.record(watch, owner_client_id)`
   — a fire-and-forget-shaped call whose exceptions are caught and logged, never propagated
   (Requirement 3.2).
4. The API response to the frontend is unchanged (Requirement 6.2).

The same `record(...)` call-out point is used on every poll outcome, cancellation, and expiry —
i.e., everywhere `WatchService` already calls `self._notifier.notify(...)`. This is deliberate:
`NotificationService` is already the "something observable just happened" seam, so
`WatchHistoryRepository` is wired in beside it rather than inventing a second seam.

### Data model — `watch_history` table

| Column             | Type          | Notes                                                   |
| ------------------ | ------------- | -------------------------------------------------------- |
| `watch_id`          | `text` PK     | Same value as the live `Watch.watch_id`                  |
| `owner_client_id`   | `text`, null  | Requirement 2.3/2.4; indexed for dashboard listing        |
| `status`            | `text`        | Mirrors `WatchStatus`; queryable without parsing `watch_json` |
| `created_at`        | `timestamptz` |                                                          |
| `updated_at`        | `timestamptz` | Drives "most recently updated first" ordering              |
| `expires_at`        | `timestamptz` |                                                          |
| `watch_json`        | `jsonb`       | The exact `Watch.model_dump_json()` payload                |

**Refined during implementation** from the original per-field breakdown (separate `query`/
`found_slots`/`booking` columns) to one `watch_json` column holding the full serialized `Watch`,
plus only the columns actually needed for ordering/filtering (`owner_client_id`, `status`,
`updated_at`). This matches the exact convention `RedisWatchRepository` already uses
(`model_dump_json()`/`model_validate_json()`) rather than inventing a second, hand-maintained SQL
schema that has to stay in sync with `backend/models/reservation.py` by hand every time a field is
added there. `asyncpg` returns a `jsonb` column as the same JSON text `model_validate_json()`
already parses, so this costs nothing over `text` while gaining Postgres-side JSON validation.

An upsert on `watch_id` keeps this a single denormalized row per watch rather than an event log —
Requirement 3.1 asks for "current state," not history-of-history. The upsert's `owner_client_id`
column uses `COALESCE(EXCLUDED.owner_client_id, watch_history.owner_client_id)`: a later call with
no owner (every poll outcome, which carries no client identity today) can never erase the owner
recorded at creation.

### Migration

A minimal, dependency-light migration runner (a versioned `.sql` file list applied in order, tracked
in a `schema_migrations` table) rather than pulling in Alembic for one table — consistent with this
project's preference for the smallest tool that satisfies the requirement. Runs at startup
(Requirement 3.4); a failed migration raises the same `ConfigurationError` pattern `WatchSettings`
already uses, so it surfaces as a 503 rather than partial startup.

### CORS

`CORSMiddleware` is added to `main.py`, configured from a new `FRONTEND_ORIGINS` setting
(comma-separated, required when set — Requirement 5.4). `allow_credentials` stays `False` since
there is no cookie-based session (no auth this milestone); the client id travels in a plain header,
which needs `allow_headers` to include `X-Dibs-Client-Id` but does not need credentialed CORS.

### Health

`GET /health` gains an additive `history_readiness` field using the same `Readiness` enum
Milestone 3's `ReadinessTracker` already defines (`ready`/`degraded`/`unknown`), fed by whether the
most recent `WatchHistoryRepository.record(...)` call succeeded — reusing the exact
evidence-based-only pattern from Milestone 3 rather than inventing a second readiness vocabulary.

## Error Handling

- **Postgres misconfigured or unreachable at startup:** **corrected during implementation** from
  this document's original plan of mirroring `WatchSettings`'s 503-everywhere contract. That would
  have let an optional, additive projection take down the core watch/orchestrator routes it has no
  business affecting — a direct contradiction of Requirement 3.2's whole point. The implemented
  behavior instead mirrors Redis's own fallback story (`_attach_redis`'s "unreachable → log a
  warning, fall back to memory" branch, not `WatchSettings`'s "invalid → 503" branch): a bad
  `POSTGRES_URL`, an unreachable server, or a failed migration are each caught in `_attach_postgres`,
  logged as an error, and leave `watch_history` disabled — startup completes normally and every
  non-history route is entirely unaffected.
- **Postgres unreachable during a projection write:** caught, logged with `watch_id` and the
  triggering outcome, `history_readiness` degrades; the triggering request/poll succeeds normally.
- **Frontend receives a non-2xx from the backend:** the `lib/api.ts` wrapper normalizes every
  failure into a typed `{ ok: false, message }` shape so no page component handles raw fetch
  rejections or renders a raw error object (Requirement 1.7).
- **Client id missing or malformed:** treated as `None` (anonymous, unscoped) rather than rejected —
  there is no authentication boundary being enforced, so a malformed header should degrade
  gracefully, not 400.

## Testing Strategy

- **`WatchHistoryRepository` unit tests** (fake/`asyncpg` test database or `sqlite`+`aiosqlite` for
  fast CI, mirroring how `fakeredis[lua]` stands in for Redis today): upsert semantics, owner
  scoping, retention-window reads for a watch already cleaned from the live store.
- **Wiring tests** (extends `test_watch_service.py`/`test_api.py` pattern): a fake history
  repository injected into `WatchService`, asserting it is called exactly once per outcome with the
  right fields, and that a raising fake never propagates into the caller's response — this is the
  direct test of Requirement 3.2 and 6.3.
- **CORS tests**: `TestClient` asserting the configured origin gets `Access-Control-Allow-Origin`
  and an unconfigured one does not, and that `allow_credentials` stays `False`.
- **Full Milestone 1–3 regression**: run unmodified as Requirement 6.1 requires; no existing
  assertion should need to change, since every new field is additive and every new code path is
  additive-only from `WatchService`'s perspective.
- **Frontend**: component-level tests for the prompt flow's five response-shape branches
  (Requirement 1.3–1.7) and the dashboard's empty/active/terminal states (Requirement 4), plus one
  integration test running the Next.js dev server against a fake API server to catch contract drift
  between `types/api.ts` and the real backend schemas.
- **No live external services** in any automated test, matching this repo's existing standard —
  the only new local infrastructure requirement is the `postgres` service the compose file already
  defines (currently unused; this milestone is what finally connects to it).
