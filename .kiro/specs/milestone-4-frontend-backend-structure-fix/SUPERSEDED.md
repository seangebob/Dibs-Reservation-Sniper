# SUPERSEDED — do not implement as written

**Status:** retired 2026-09-03. This spec is kept for its analysis, not as a plan.
Its `- [ ]` checkboxes in `tasks.md` are **not** pending work.

## Why

It was written 2026-08-31, which predates Milestone 4 tasks 8–12 and the whole of Milestone 5. It
audits a codebase that no longer exists in that shape, and three of its "defects" are decisions the
project has since made deliberately and covered with passing tests. Implementing it as written
would regress shipped behavior.

It is also, in scope, a different project: 4 parent tasks, ~31 leaf tasks, a 30-wave dependency
graph, 23 design properties, and subsystems (a token-fenced durable delivery outbox with
`PENDING`/`IN_FLIGHT`/`UNCERTAIN`/`EXHAUSTED` states and operator resolution, an `AuthorityGuard`
with epoch/token renewal, SHA-256 migration provenance verified against a built wheel, worker health
via PID-bound files on disk) that are disproportionate to a product still booking against a mock
provider.

## What was verified as genuinely real

Each of these was confirmed against the code on 2026-09-03, not taken on the spec's word.

**Carried forward into Milestone 6** (`docs/specs/milestone-6-notification-delivery/`):

| Claim | Finding | Where it went |
| --- | --- | --- |
| 1.1 | The Celery worker builds `WatchService` with no `history=` and never reads `POSTGRES_URL`, while `infra/docker-compose.yml` passes it with a comment claiming background polls are recorded | M6 Task 7 |
| 1.24 | `LoggingNotificationService` logs venue, date, and party size | M6 Task 3 |
| 1.25 | `notify()` is bare-awaited at all five call sites ahead of the history write, so a notifier failure escapes a committed transition | M6 Task 2 |

**Fixed 2026-09-04**, after Milestone 6 made the second one user-visible:

| Claim | Finding | Fix |
| --- | --- | --- |
| 1.13 | The Postgres DSN — password included — was interpolated into a `ConfigurationError` that `main.py` logs at startup | `config.redact_dsn` renders scheme/host/port/database only, dropping userinfo and the query string whole. Applied at both leak sites (the invalid-scheme error too, which the audit missed), and `PostgresSettings.dsn` is now `repr=False` so no traceback rendering discloses it. |
| 1.15 | Recovery expiry called `expire_if_eligible` on the repository directly, so a sweep-expired watch was never projected and — after M6 — its owner was never emailed | New `WatchService.expire`, mirroring `cancel`: the same repository transition, then the projection write and a notification gated on the `event_id` the expire script already issues at most once. `RecoveryCoordinator` takes an optional `watch_service` and falls back to the bare repository call when it has none. |

**Still open — real, verified, and not yet scheduled:**

| Claim | Finding | Location |
| --- | --- | --- |
| 1.30 | The worker container inherits the image's API HTTP healthcheck and is therefore permanently "unhealthy" | `Dockerfile:40` + the `worker` service in `infra/docker-compose.yml` |
| 1.26 | `smembers` reads the entire watch/active index | `backend/db/repositories/watches.py:892,896,1510` |
| 1.4 | The projection upsert has no revision guard, so out-of-order writes can overwrite newer state | `backend/db/repositories/watch_history.py` |
| 1.3 | A failed first history write loses the owner association permanently | `backend/services/watch_service.py` |
| 1.29 | Migrations are tracked by version only, with no checksum, so an edited applied migration goes undetected | `backend/db/postgres.py` |
| 1.8 | `/api/watches/mine` orders by `updated_at` and silently truncates at 100 | `backend/api/routes/watches.py` |

1.30 is small and worth doing. The rest are real but not urgent at this scale, and none of them is
a reason to implement this spec as written.

## What is rejected outright

These would regress behavior that is shipped, deliberate, and covered by passing tests:

- **1.6** — wants a malformed `X-Dibs-Client-Id` to return 422. Milestone 4 deliberately degrades it
  to anonymous and asserts so in `tests/test_watch_owner_scoping.py`.
- **1.7** — wants unavailable history to return 503 instead of an empty list. This **directly
  contradicts Milestone 5 Requirement 3.5** ("ownership enforcement and my-watches behave as the
  anonymous case rather than failing the request"), which is implemented and tested.
- **1.10** — wants a configured-but-broken capability to fail startup. The project's stated
  philosophy is the opposite: degrade, never gate.

## What is simply stale

- Task 3.8 reserves `backend/db/migrations/0002_*.sql`; Milestone 5 already took that number with
  `0002_accounts.sql`.
- Task 3.10 says "do not create `apps/web`"; the repo was flattened to `frontend/` during M4.
- Tasks 1.14 and 1.32 claim the suite does not test X, citing "tasks 1–7". M4 ran to task 12 and M5
  added ~103 backend tests.
- Task 3.24's gate forbids changing authentication behavior without approval. Authentication shipped
  in Milestone 5.
