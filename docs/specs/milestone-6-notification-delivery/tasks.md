# Implementation Plan

Each task is independently testable and leaves the suite green. Task 1 locks today's behavior, and
Task 2 makes notification safe to fail *before* Task 4 puts a real mail server behind it — the
ordering matters, because reversing it would briefly make a mail outage able to corrupt a watch
transition.

- [ ] 1. Characterize and lock the pre-notification baseline
  - Write tests proving today's contract before delivery exists: the logging notifier is the default
    when none is injected; a terminal transition calls `notify` exactly once per event; the worker's
    built service currently carries neither a history recorder nor a notifier; and no route or model
    exposes a recipient. Record the current `notify`-before-`record` ordering as characterization so
    Task 2's reversal is a visible, deliberate change.
  - Run `python -m pytest`, `python -m mypy backend`, `python -m compileall backend tests`,
    `git diff --check`.
  - _Requirements: 6.4, 6.5_

- [ ] 2. Make notification best-effort, bounded, and second
  - Add `WatchService._notify(watch, event)` mirroring the existing `_record_history` helper: catches
    every exception, logs a warning, never raises. Wrap the call in `asyncio.wait_for` with a
    configurable timeout so a hung relay cannot outlive a Milestone 3 lease.
  - Replace all five bare `await self._notifier.notify(...)` call sites (`watch_service.py` 666, 907,
    921, 952, 989) with `_notify`, and reorder each so `_record_history` runs first.
  - _Bug_Condition: a raising or hanging notifier propagates out of a committed terminal transition,
    skips the projection write, and causes a poll retry_
  - _Expected_Behavior: the poll result, committed status, and history write are identical whether
    the notifier succeeds, raises, or times out_
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [ ] 3. Add email configuration and strip the log line
  - Add `EmailSettings` to `config.py` following `AccountSettings`' bounded `from_environment()`
    pattern (host, port, username, password, from, STARTTLS, timeout, dashboard base URL). An unset
    `SMTP_HOST` means disabled; a set host with no `SMTP_FROM` raises `ConfigurationError`. Keep the
    password out of every representation.
  - Remove `venue`, `date`, and `party` from `LoggingNotificationService.notify`'s log line, keeping
    `watch_id`, `event`, and `attempts`.
  - _Requirements: 5.2, 5.3, 5.4, 6.1, 6.2_

- [ ] 4. Build the email notification service
  - Add `backend/integrations/email.py`: a pure message composer (subject naming venue + outcome,
    plain-text body with the dashboard link, booking confirmation id on `BOOKED`), an injectable
    `SmtpSender` wrapping `smtplib` under `asyncio.to_thread`, and `EmailNotificationService`
    implementing the existing `NotificationService` protocol.
  - No recipient ⇒ return without sending and without logging an error.
  - _Bug_Condition: terminal watch events reach no one outside the application log_
  - _Expected_Behavior: an account-owned terminal event produces exactly one addressed message
    describing the outcome; delivery failures are logged and abandoned, never retried_
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 6.3_

- [ ] 5. Resolve the recipient from the projection
  - Add `backend/services/recipients.py`: `RecipientResolver` composing the two methods Milestone 5
    already shipped — `WatchHistoryRepository.get_account_owner(watch_id)` then
    `AccountRepository.get_by_id(user_id)` — returning the address or `None`. Add no SQL and no
    repository method; add no field to the public `Watch`.
  - `None` for an anonymous watch, a missing projection, or a deleted account, in every case without
    raising.
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

- [ ] 6. Wire the notifier into the API process
  - Build the resolver and `EmailNotificationService` in `_attach_postgres` alongside `AuthService`,
    behind the same `ConfigurationError` degradation: bad email settings log and fall back to the
    logging notifier rather than failing startup. Pass `notifier=` into the `WatchService` the API
    composes — a wiring point that does not exist today at all.
  - _Requirements: 5.1, 5.2_

- [ ] 7. Give the Celery worker the projection and the notifier
  - In `monitor_watch.py`, build a cached asyncpg pool from `PostgresSettings` when `POSTGRES_URL` is
    configured and pass `history=WatchHistoryRepository(pool)` and the same `notifier=` into
    `build_watch_service()`. With no PostgreSQL configured, behave exactly as today.
  - Close the pool in `_close_worker_resources()` alongside Redis, exactly once, under the existing
    lock and idempotence guard.
  - _Bug_Condition: background poll outcomes reach neither the durable projection nor a
    notification, while `docker-compose.yml` passes the worker `POSTGRES_URL` claiming they do_
  - _Expected_Behavior: an in-process poll and a Celery poll produce the same projection row and the
    same delivery_
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

- [ ] 8. Full validation
  - Backend `python -m pytest` (full suite — M1–5 regressions plus M6), `python -m mypy backend`,
    `python -m compileall backend tests`, `git diff --check`. Frontend `typecheck` + `vitest` +
    `next build` (unchanged by this milestone; run to prove it).
  - Add a privacy sentinel test scanning captured log records for venue, date, party size, recipient
    address, and SMTP password across a full terminal-transition run.
  - Confirm every Requirement 1–6 acceptance criterion has a corresponding passing test, that the M4
    contract-drift and M4/M5 preservation baselines pass with their files unmodified, and that no
    Milestone 1–5 assertion was weakened.
  - Update `README.md`: add a Milestone 6 section, and correct the now-false "There is **no
    authentication**" line left over from Milestone 4.
  - _Requirements: all_
