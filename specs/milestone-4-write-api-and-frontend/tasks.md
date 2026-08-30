# Implementation Plan

- [x] 1. Characterize current behavior before adding the history/frontend seam
  - Write tests proving today's contract: `WatchService.create`/poll/cancel take no owner concept,
    `GET /api/watches` is globally unscoped, `/health` has no history field, and no CORS headers are
    sent on any response. These are the preservation baselines Requirement 6 checks against.
  - Run `python -m pytest`, `python -m compileall backend tests`, `git diff --check`; no live
    services.
  - _Requirements: 6.1, 6.2_

- [x] 2. Add PostgreSQL settings, connection, and migration runner
  - Add `PostgresSettings` to `config.py` (DSN, pool size, statement timeout) following the exact
    `from_environment()`/bounded-validation pattern `WatchSettings` already uses; a missing or
    malformed DSN raises `ConfigurationError`.
  - Add `backend/db/postgres.py`: engine/pool factory plus a minimal ordered-`.sql`-file migration
    runner tracked in a `schema_migrations` table, run once at startup.
  - _Bug_Condition: no database engine, settings, or migration path exists_
  - _Expected_Behavior: startup fails loudly with `ConfigurationError` on bad config; a reachable,
    unmigrated database is migrated automatically before serving requests_
  - _Requirements: 3.4, 5.4_

- [x] 3. Add the `watch_history` schema and `WatchHistoryRepository`
  - Write the migration for the `watch_history` table (see design.md's data model).
  - Implement `WatchHistoryRepository.record(watch, owner_client_id)` (upsert on `watch_id`),
    `list_for_owner(owner_client_id)`, and `get(watch_id)` (returns the durable projection even
    after live-store retention cleanup).
  - _Bug_Condition: no durable, queryable record of a watch exists once Milestone 3's live-store
    retention window elapses_
  - _Expected_Behavior: a watch's last known public state is readable from Postgres indefinitely
    (or per a documented history-retention setting), independent of the live store's lifecycle_
  - _Requirements: 3.1, 3.3_

- [x] 4. Wire `WatchHistoryRepository` into `WatchService` as a passive observer
  - Add an optional constructor parameter; call `record(...)` beside every existing
    `self._notifier.notify(...)` call site (creation, each poll outcome, cancellation, expiry).
  - Wrap every call in try/except that logs and never propagates or delays the triggering request.
  - _Bug_Condition: a Postgres outage would otherwise block or fail a live poll/creation, or a
    watch's history would silently go stale with no observable signal_
  - _Expected_Behavior: history recording never affects the caller-visible outcome or timing of any
    watch operation; a recording failure is logged and reflected only in `history_readiness`_
  - _Preservation: `WatchService`'s claim/fencing/notification logic is byte-identical; only new
    call-outs are added_
  - _Requirements: 3.2, 6.3_

- [ ] 5. Thread the anonymous client identifier through creation and listing
  - Accept `X-Dibs-Client-Id` in the watch-creation route and `PromptRouter`'s entry point; pass it
    to `WatchService.create` as `owner_client_id: str | None = None`.
  - Add owner-scoped listing (`GET /api/watches?owner=...` or a dedicated route) backed by
    `WatchHistoryRepository.list_for_owner`, separate from the existing unscoped `list_active`/
    `list_all` behavior which stays as-is.
  - _Bug_Condition: no way exists to ask "which watches belong to this anonymous visitor"_
  - _Expected_Behavior: a request with a client id gets only that id's watches from the scoped
    endpoint; a request with none still succeeds exactly as today via the existing endpoints_
  - _Preservation: existing `POST`/`GET`/`DELETE /api/watches` contracts, status codes, and body
    shapes unchanged; `owner_client_id` absent/`None` never appears in the public `Watch` model_
  - _Requirements: 2.2, 2.3, 2.4, 6.2_

- [ ] 6. Add CORS middleware and `FRONTEND_ORIGINS` configuration
  - Add `CORSMiddleware` configured from a new required-when-set setting; allow the
    `X-Dibs-Client-Id` header and the methods the frontend needs; `allow_credentials=False`.
  - Assert via `TestClient` that a configured origin gets the right headers and an unconfigured one
    does not.
  - _Bug_Condition: a browser-based frontend on a different origin cannot call the API at all_
  - _Expected_Behavior: configured origins can call the API from a browser; the API's non-browser
    behavior (scripts, tests, curl) is unaffected, since CORS headers are additive response headers_
  - _Requirements: 5.1, 5.4_

- [ ] 7. Additive `history_readiness` health field
  - Track the last `WatchHistoryRepository.record(...)` outcome using Milestone 3's existing
    `Readiness`/`ReadinessTracker` vocabulary; expose it additively on `/health`.
  - _Preservation: every existing `/health` field and the top-level `status` meaning unchanged_
  - _Requirements: 6.4_

- [ ] 8. Scaffold the Next.js app in `apps/web`
  - Standard Next.js app-router project; `lib/client-id.ts` (generate/persist an opaque id in
    `localStorage` on first load); `lib/api.ts` (typed fetch wrapper that always attaches
    `X-Dibs-Client-Id` and normalizes non-2xx responses into `{ ok: false, message }`).
  - _Requirements: 2.1, 2.2_

- [ ] 9. Build the prompt page
  - Single input, submit action, disabled-while-in-flight state; render branches for
    `CLARIFICATION_REQUIRED`, `AVAILABILITY_FOUND`, `NO_AVAILABILITY`, `MOCK_BOOKED` (mock-labeled),
    `WATCH_CREATED`, and the generic-failure state.
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7_

- [ ] 10. Build the watch dashboard page
  - List owned watches (most recent first) with venue/party/date/status/attempts; show next-check
    time and a cancel action for `ACTIVE` watches; visually distinguish `FOUND`/`BOOKED` from
    `ACTIVE`; empty state linking back to the prompt page.
  - Cancel action calls `DELETE /api/watches/{watch_id}` and updates local state without a reload.
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

- [ ] 11. Add the optional `web` compose service and document deployment
  - Extend `infra/docker-compose.yml`'s `app` profile with a `web` service built from
    `apps/web`, reading the compose-network API URL; existing `redis`/`postgres`/`api`/`worker`
    service definitions, ports, images, and health checks untouched (static test compares them).
  - Update `README.md` with the new `PostgreSQL`/`FRONTEND_ORIGINS` settings, the migration-at-
    startup behavior, and how to run frontend + backend together locally (PowerShell-friendly, both
    commands marked as manual long-running processes).
  - _Bug_Condition: no documented or optional path exists to run frontend + backend + Postgres
    together_
  - _Expected_Behavior: `docker compose --profile app up` optionally brings up all four services;
    the default (no profile) compose flow is byte-identical to before this milestone_
  - _Requirements: 5.2, 5.3_

- [ ] 12. Full validation
  - Backend: `python -m pytest` (full suite — Milestone 1–3 regressions plus this milestone's new
    tests), `python -m mypy backend`, `python -m compileall backend tests`, `git diff --check`.
  - Frontend: component tests for both pages' response/state branches; one contract-drift test
    against the real backend schemas.
  - Confirm every Requirement 1–6 acceptance criterion has a corresponding passing test, and that no
    Milestone 1–3 assertion was weakened or rewritten to accommodate this milestone.
  - _Requirements: all_
