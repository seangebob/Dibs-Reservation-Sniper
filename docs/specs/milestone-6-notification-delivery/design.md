# Design Document

## Overview

Milestone 6 makes Dibs actually tell someone. It adds an SMTP notification service behind the
existing `NotificationService` protocol, resolves the recipient from Milestone 5's accounts, and
wires both the API and the Celery worker to use it — while making the notification call site
best-effort so outbound delivery can never damage a committed watch transition.

Guiding principle: **notification is a side effect of a committed transition, never a participant
in it.** This mirrors exactly how Milestone 4 treated the history projection — `_record_history`
already swallows its failures and never delays a caller. Notification gets the same treatment,
because today it does not have it.

## Architecture

```
WatchService (terminal transition already committed to the repository)
  │
  ├─ 1. _record_history(watch)      ← durable projection first (best-effort, existing)
  └─ 2. _notify(watch, event)       ← best-effort + timeout (NEW isolation)
         │
         └─ EmailNotificationService
              ├─ RecipientResolver ──▶ WatchHistoryRepository.get_account_owner(watch_id)
              │                        AccountRepository.get_by_id(user_id).email
              └─ SmtpSender ─────────▶ asyncio.to_thread(smtplib) with timeout
```

Both roles compose the same objects:

```
API process   : _attach_postgres → history recorder + notifier → WatchService
Celery worker : build_watch_service → same recorder + same notifier   ← NEW (was neither)
```

New backend modules (mirroring the existing layout):

| Module | Responsibility |
| --- | --- |
| `backend/integrations/email.py` | `EmailNotificationService`, `SmtpSender`, message composition |
| `backend/services/recipients.py` | `RecipientResolver` — watch id → account email, or `None` |
| `backend/config.py` (extended) | `EmailSettings` with bounded `from_environment()` |

## Key decisions

- **Reuse both repository methods; add no SQL.** Milestone 5 already shipped
  `WatchHistoryRepository.get_account_owner(watch_id) -> UUID | None` and
  `AccountRepository.get_by_id(user_id) -> User | None`. Composing them resolves an address with
  zero new queries and zero new repository methods. It costs one extra round trip versus a join;
  if that ever matters, the join is a drop-in replacement behind the same resolver interface.
- **`user_id` still never touches the public `Watch`.** The resolver starts from `watch_id` and
  reads the projection, exactly as Milestone 5's ownership enforcement does. The M4 contract-drift
  test stays green untouched.
- **Best-effort, at-most-once, no outbox.** A failed send is logged and dropped. This deliberately
  rejects a durable delivery state machine: the alternative (`PENDING`/`IN_FLIGHT`/`UNCERTAIN`
  states, leases, dead-lettering, operator resolution) is a large subsystem to guarantee delivery
  of a convenience email whose information is already durable on the dashboard. Not retrying is
  also what keeps a poll retry from emailing someone twice. If delivery proves lossy in practice,
  a durable outbox is the upgrade path and this interface does not change.
- **`smtplib` on a worker thread with a timeout.** `smtplib` is blocking, so it runs under
  `asyncio.to_thread` wrapped in `asyncio.wait_for`. The timeout matters more than usual: a poll
  holds a Milestone 3 lease while it runs, so an unbounded SMTP hang would be a coordination bug,
  not just slowness.
- **History before notification.** Today `notify()` runs *before* `_record_history` at every call
  site. Reversing that puts the durable record first, so a delivery failure never costs the
  dashboard its terminal state. No test asserts the current ordering.

## The notification call sites

**Corrected by Task 1's characterization tests.** An earlier draft of this document
claimed all five sites notify before recording. They do not, and the difference matters:

| Site | Path | Order today | Gated at-most-once? |
| --- | --- | --- | --- |
| `666` | primary (fenced, windowed) | `record` → `notify` ✅ | **yes** — on `result.event_id` |
| `907`, `921`, `952`, `989` | legacy (no runtime sidecar) | `notify` → `record` ❌ | no |

Two consequences:

- **Milestone 3 already delivers at-most-once on the primary path.** `_commit_window` writes
  history on `COMMITTED`, and the notification is gated on a terminal event id issued at most once
  per transition. This is why Milestone 6 needs no delivery state machine — the guarantee the
  retired `.kiro` spec wanted to build already exists where it counts.
- **Only the four legacy sites need reordering.** They become:

```python
await self._record_history(found)                                   # durable first
await self._notify(found, WatchEvent.AVAILABILITY_FOUND)            # best-effort
```

**All five need the isolation**, including the already-correctly-ordered primary one, because every
site is a bare `await` today:

```python
await self._notifier.notify(committed, event)   # no guard, no timeout — the real defect
```

where `_notify` mirrors the existing `_record_history` helper exactly:

```python
async def _notify(self, watch: Watch, event: WatchEvent) -> None:
    """Best-effort outbound announcement. Never raises and never delays the
    caller past the configured timeout: a failing mail server must not change
    a committed watch outcome (Requirement 3.1)."""
    try:
        await asyncio.wait_for(
            self._notifier.notify(watch, event), self._notify_timeout_seconds
        )
    except Exception:
        logger.warning(
            "watch notification failed",
            extra={"watch_id": watch.watch_id, "event": event.value},
            exc_info=True,
        )
```

This is the single most important change in the milestone: without it, adding real SMTP would let
a mail outage propagate out of a committed transition, skip the projection write, and get the poll
retried — sending a duplicate email on the retry.

## Configuration

`EmailSettings.from_environment()`, following `AccountSettings`' bounded pattern:

| Variable | Default | Notes |
| --- | --- | --- |
| `SMTP_HOST` | unset | Unset ⇒ email disabled, logging notifier retained (Req 5.1) |
| `SMTP_PORT` | `587` | Bounded 1–65535 |
| `SMTP_USERNAME` | unset | Optional (open relays / local MTAs) |
| `SMTP_PASSWORD` | unset | Never logged or represented (Req 6.2) |
| `SMTP_FROM` | unset | Required when `SMTP_HOST` is set |
| `SMTP_STARTTLS` | `true` | |
| `SMTP_TIMEOUT_SECONDS` | `10` | Bounded 1–60; also the `_notify` timeout |
| `DASHBOARD_BASE_URL` | `http://localhost:3000` | For the link in the message |

Invalid values raise `ConfigurationError`; the composition layer catches it, logs, and keeps the
logging notifier — the same degradation `AccountSettings` already gets in `_attach_postgres`.

## Worker composition

`backend/workers/tasks/monitor_watch.py` currently builds `WatchService(...)` with neither
`history=` nor `notifier=`, and never reads `POSTGRES_URL`. It gains:

- a cached asyncpg pool built from `PostgresSettings` when configured,
- `history=WatchHistoryRepository(pool)` (no readiness decorator — that tracks the API's `/health`),
- `notifier=` the same email notifier the API builds,
- both closed in `_close_worker_resources()` alongside the existing Redis cleanup, under the same
  `_runner_lock` and idempotence guard.

With no `POSTGRES_URL` the worker behaves exactly as today (Req 4.3).

## Privacy

`LoggingNotificationService.notify` currently logs `venue`, `date`, and `party` (Req 6.1). Those
fields are removed; `watch_id`, `event`, and `attempts` remain, which is enough to correlate a
delivery with a watch without putting someone's reservation in a log aggregator. The recipient
address is never logged, and `EmailSettings` keeps `repr=False` on its password field.

## Testing strategy

| Area | Approach |
| --- | --- |
| Message composition | Pure function over a `Watch` + event; assert subject/body/link/confirmation id |
| SMTP transport | Inject a fake sender; assert auth/TLS/timeout calls without a live relay |
| Recipient resolution | Fake repositories: account-owned, anonymous, no projection, deleted account |
| Isolation (Req 3) | A notifier that raises and one that hangs; assert identical poll results, history still written, no retry |
| Ordering (Req 3.4) | Assert `record` is called before `notify` |
| Worker parity | Assert the worker's built service carries both collaborators when configured, neither when not |
| Privacy | Sentinel scan for venue/date/party/address/password across captured log records |
| Preservation | Full M1–5 suite unmodified; `/health` and contract-drift tests untouched |

No test contacts a live SMTP server, PostgreSQL, or Redis.
