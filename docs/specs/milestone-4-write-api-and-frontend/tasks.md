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

- [x] 5. Thread the anonymous client identifier through creation and listing
  - Accept `X-Dibs-Client-Id` in the watch-creation route and `PromptRouter`'s entry point; pass it
    to `WatchService.create` as `owner_client_id: str | None = None`.
  - Add owner-scoped listing (`GET /api/watches?owner=...` or a dedicated route) backed by
    `WatchHistoryRepository.list_for_owner`, separate from the existing unscoped `list_active`/
    `list_all` behavior which stays as-is.
  - **Scope correction found during implementation:** the scoped-listing endpoint needs a live
    `WatchHistoryRepository` reachable from the app, but no earlier task actually connected
    PostgreSQL into `main.py`'s lifespan (tasks 2-4 only built the standalone pieces). Folded that
    wiring into this task rather than leaving it implicit — see design.md's Error Handling section
    for the resulting `_attach_postgres` failure-handling decision (corrected from a first draft
    that would have mirrored `WatchSettings`'s 503-on-error contract).
  - _Bug_Condition: no way exists to ask "which watches belong to this anonymous visitor"_
  - _Expected_Behavior: a request with a client id gets only that id's watches from the scoped
    endpoint; a request with none still succeeds exactly as today via the existing endpoints_
  - _Preservation: existing `POST`/`GET`/`DELETE /api/watches` contracts, status codes, and body
    shapes unchanged; `owner_client_id` absent/`None` never appears in the public `Watch` model_
  - _Requirements: 2.2, 2.3, 2.4, 6.2_

- [x] 6. Add CORS middleware and `FRONTEND_ORIGINS` configuration
  - Add `CORSMiddleware` configured from a new required-when-set setting; allow the
    `X-Dibs-Client-Id` header and the methods the frontend needs; `allow_credentials=False`.
  - Assert via `TestClient` that a configured origin gets the right headers and an unconfigured one
    does not.
  - **Implementation note:** evaluated in `create_app()`, not `lifespan()`, because Starlette
    forbids adding middleware after the ASGI app has been built (its first call, which precedes
    `lifespan()`). A malformed `FRONTEND_ORIGINS` degrades to CORS-disabled with a logged error
    rather than crashing startup, matching `_attach_postgres`'s established failure philosophy for
    optional features. Also exposes the existing `X-Watch-Monitoring-Policy`/
    `X-Watch-Max-Availability-Checks`/`Warning` response headers via `expose_headers`, since a
    browser's `fetch()` cannot read a custom header unless the server explicitly exposes it — without
    this, Requirement 1's UI could never show the monitoring-policy disclosure it already receives.
  - _Bug_Condition: a browser-based frontend on a different origin cannot call the API at all_
  - _Expected_Behavior: configured origins can call the API from a browser; the API's non-browser
    behavior (scripts, tests, curl) is unaffected, since CORS headers are additive response headers_
  - _Requirements: 5.1, 5.4_

- [x] 7. Additive `history_readiness` health field
  - Track the last `WatchHistoryRepository.record(...)` outcome using Milestone 3's existing
    `Readiness`/`ReadinessTracker` vocabulary; expose it additively on `/health`.
  - **Implementation:** `ReadinessTracker` gained a symmetric `history_state`/
    `record_history_outcome(*, ok: bool)`/`history_readiness` triple. A new
    `TrackingHistoryRecorder` decorator (in `watch_history.py`, next to the existing
    `WatchHistoryRecorder` Protocol) wraps the real repository so every write updates the tracker
    and passes through unchanged (re-raising exceptions so `WatchService`'s existing try/except sees
    the same shape). `_attach_postgres` splits its wiring in two: `app.state.watch_history` stays
    the raw `WatchHistoryRepository` for `/api/watches/mine` reads, `app.state.watch_history_recorder`
    is the decorated writer handed to `WatchService`. `/health` gains one additive
    `history_readiness` field.
  - _Preservation: every existing `/health` field and the top-level `status` meaning unchanged_
  - _Requirements: 6.4_

- [x] 8. Scaffold the Next.js app in `apps/web`
  - Standard Next.js app-router project; `lib/client-id.ts` (generate/persist an opaque id in
    `localStorage` on first load); `lib/api.ts` (typed fetch wrapper that always attaches
    `X-Dibs-Client-Id` and normalizes non-2xx responses into `{ ok: false, message }`).
  - **Design first:** published a warm-editorial visual direction (prompt page, watch dashboard,
    result-states sheet) as a design canvas before writing pixels; the visual direction only lands
    in code at Tasks 9–10, so this task's scaffold presumes no approval.
  - **Built:** Next.js 15.5.25 (App Router, React 19, TS strict) under `apps/web/`. `lib/client-id.ts`
    (SSR-safe, `crypto.randomUUID`, session-cached, private-mode fallback), `lib/api.ts`
    (`apiFetch` → discriminated `ApiResult`, reads the backend's `{detail}` error shape, typed
    `parseAndBook`/`listMyWatches`/`cancelWatch`/`createWatch` wrappers), `types/api.ts`
    (hand-mirrored backend contracts). Vitest + jsdom; 14 unit tests. `.env.local.example` for
    `NEXT_PUBLIC_API_BASE_URL`.
  - **Local run verified:** `npm install` → `typecheck` → `test` (14 pass) → `build` (4 routes
    prerender), all green on Node 24. Build artifacts (`node_modules`, `.next`) gitignored.
  - _Requirements: 2.1, 2.2_

- [x] 9. Build the prompt page
  - Single input, submit action, disabled-while-in-flight state; render branches for
    `CLARIFICATION_REQUIRED`, `AVAILABILITY_FOUND`, `NO_AVAILABILITY`, `MOCK_BOOKED` (mock-labeled),
    `WATCH_CREATED`, and the generic-failure state.
  - **Built (Night Scope):** `app/page.tsx` is a server-rendered shell (masthead, hero, scope
    furniture in `_components/Scope.tsx`) wrapping one client island, `_components/PromptConsole.tsx`,
    which owns the `idle → loading → result | error` state machine and locks the input + presets while
    a request is in flight (no double-fire). `_components/ResultView.tsx` is a pure, per-status
    renderer (WATCH_CREATED, AVAILABILITY_FOUND, MOCK_BOOKED with a demo-only disclaimer,
    NO_AVAILABILITY, CLARIFICATION_REQUIRED, WATCH_REQUIRED/generic, plus loading + SIGNAL-LOST error
    with retry). `lib/format.ts` defensively reads the opaque `intent` for the branches that carry no
    slot/booking (venue/party/date). Design system lives in `app/globals.css` as tokens; fonts load
    via a runtime `<link>` (no build-time network). The whole error surface flows from `api.ts`'s
    normalized `{ ok:false, message }` — no component sees a raw fetch failure.
  - **Validation:** `typecheck` clean, `vitest run` 23 pass (added 9 `lib/format.test.ts` cases for
    intent extraction + date/time formatting), `next build` green (`/` prerenders static, console is
    the sole client chunk). React component-render tests for each branch are deferred to Task 12,
    where Testing Library is added.
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7_

- [x] 10. Build the watch dashboard page
  - List owned watches (most recent first) with venue/party/date/status/attempts; show next-check
    time and a cancel action for `ACTIVE` watches; visually distinguish `FOUND`/`BOOKED` from
    `ACTIVE`; empty state linking back to the prompt page.
  - Cancel action calls `DELETE /api/watches/{watch_id}` and updates local state without a reload.
  - **Built (Night Scope):** `app/watches/page.tsx` is a server-rendered shell (masthead, heading,
    scope furniture) wrapping one client island, `_components/WatchDashboard.tsx`, which loads the
    calling client's own watches via `listMyWatches()` on mount and owns the
    `loading → error | empty | list` states. It sorts most-recent-first, cancels in place via
    `cancelWatch()` (replacing the row with the resolved terminal `Watch`, no refetch — Req 4.5),
    surfaces a cancel failure as a banner, and ticks an injected `now` clock every 30s so the "next
    check in …" countdowns stay honest. `_components/WatchCard.tsx` is a pure per-watch renderer: a
    colour rail + status pill make ACTIVE/FOUND/BOOKED/EXPIRED/CANCELLED scannable (Req 4.3), with
    attempts `n / max`, next-check countdown + `CANCEL WATCH` for ACTIVE only, and a booking id for
    BOOKED. The masthead was extracted to `_components/Masthead.tsx` (shared with the prompt page)
    so the nav stays in sync; `/watches` now exists, clearing the prompt page's previously-dead
    links. `lib/format.ts` gained `formatCountdown()` (injectable `now`, deterministic under test).
  - **Validation:** `typecheck` clean, `vitest` 26 pass (+3 `formatCountdown` cases), `next build`
    green (`/watches` prerenders static, 3.14 kB). Live: `/api/watches/mine` → `[]` empty-state
    path, `DELETE` returns a full renderable `Watch` (200) and a 404 `{detail}` for an unknown id
    (drives the cancel banner), all with CORS. Full-list + cancel render behind a live Postgres
    history projection (`POSTGRES_URL`); component-render tests remain deferred to Task 12.
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

- [x] 11. Add the optional `web` compose service and document deployment
  - Extend `infra/docker-compose.yml`'s `app` profile with a `web` service built from
    `apps/web`, reading the compose-network API URL; existing `redis`/`postgres`/`api`/`worker`
    service definitions, ports, images, and health checks untouched (static test compares them).
  - Update `README.md` with the new `PostgreSQL`/`FRONTEND_ORIGINS` settings, the migration-at-
    startup behavior, and how to run frontend + backend together locally (PowerShell-friendly, both
    commands marked as manual long-running processes).
  - **Built:** added an app-profiled `web` service to `infra/docker-compose.yml` built from
    `../frontend` (the source moved from `apps/web` in the repo reorg), a multi-stage
    `frontend/Dockerfile` (Next.js `output: "standalone"`, non-root, healthcheck) + `.dockerignore`,
    and `output: "standalone"` in `next.config.mjs`. README gained a **Milestone 4** section:
    the `POSTGRES_URL` / `FRONTEND_ORIGINS` / `NEXT_PUBLIC_API_BASE_URL` settings table,
    migration-at-startup behavior, and separate-terminal PowerShell run steps for
    frontend + backend, plus the full-stack `--profile app up` path.
  - **Scope corrections found during implementation:** (1) the task's "reading the compose-network
    API URL" is wrong for a browser bundle — `NEXT_PUBLIC_API_BASE_URL` is inlined at build time and
    the *browser* calls the API, so `web` bakes the api's **host-published** URL
    (`http://localhost:8000`), never `http://api:8000`. (2) A `web` frontend that cannot reach `api`
    would be incoherent, so `FRONTEND_ORIGINS` (CORS), `POSTGRES_URL` (dashboard data), and an
    `OPENAI_API_KEY`/`OPENAI_MODEL`/`RESERVATION_TIMEZONE` passthrough were added **additively** to
    the `api`/`worker` `environment` (and `postgres` to their `depends_on`). This does not touch what
    the static test pins — images, ports, health checks, `profiles: [app]`, and the worker command —
    all still assert green.
  - **Validation:** `next build` green with standalone `server.js` emitted; `tests/test_container_
    assets.py` passes (compose contract preserved); compose YAML parses and the default no-profile
    `up` still starts only redis + postgres. Docker image build/run not executed here (no Docker
    daemon on PATH), but the build steps (`npm ci` + `npm run build`) and standalone layout are
    verified locally.
  - _Requirements: 5.2, 5.3_

- [ ] 12. Full validation
  - Backend: `python -m pytest` (full suite — Milestone 1–3 regressions plus this milestone's new
    tests), `python -m mypy backend`, `python -m compileall backend tests`, `git diff --check`.
  - Frontend: component tests for both pages' response/state branches; one contract-drift test
    against the real backend schemas.
  - Confirm every Requirement 1–6 acceptance criterion has a corresponding passing test, and that no
    Milestone 1–3 assertion was weakened or rewritten to accommodate this milestone.
  - _Requirements: all_
