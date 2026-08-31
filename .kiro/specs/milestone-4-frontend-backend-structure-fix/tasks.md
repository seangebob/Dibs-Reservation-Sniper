# Implementation Plan

## Overview

This plan repairs only the frontend-facing backend seams introduced by completed tasks 1–7 of `specs/milestone-4-write-api-and-frontend/tasks.md`. It does not implement `apps/web` or redesign the future frontend. Work proceeds test-first: reproduce every bug-condition family on the unfixed code, capture Milestone 1–3 and completed Milestone 4 preservation baselines, implement the application/storage/composition boundaries in dependency order, re-run the same properties, and finish with the full deterministic suite.

The repository's existing property-testing convention is bounded finite-domain parameterization and fixed-seed generated traces under pytest. Reuse that convention; do not add a property-testing dependency. No task may contact live PostgreSQL, Redis, Celery, a broker, a browser, OpenAI, or an external provider, and no task starts a development server or watcher.

## Task Dependency Graph

```json
{
  "waves": [
    {
      "wave": 1,
      "tasks": ["1"]
    },
    {
      "wave": 2,
      "tasks": ["2"]
    },
    {
      "wave": 3,
      "tasks": ["3.1"]
    },
    {
      "wave": 4,
      "tasks": ["3.2"]
    },
    {
      "wave": 5,
      "tasks": ["3.3"]
    },
    {
      "wave": 6,
      "tasks": ["3.4"]
    },
    {
      "wave": 7,
      "tasks": ["3.5"]
    },
    {
      "wave": 8,
      "tasks": ["3.6"]
    },
    {
      "wave": 9,
      "tasks": ["3.7"]
    },
    {
      "wave": 10,
      "tasks": ["3.8"]
    },
    {
      "wave": 11,
      "tasks": ["3.9"]
    },
    {
      "wave": 12,
      "tasks": ["3.10"]
    },
    {
      "wave": 13,
      "tasks": ["3.11"]
    },
    {
      "wave": 14,
      "tasks": ["3.12"]
    },
    {
      "wave": 15,
      "tasks": ["3.13"]
    },
    {
      "wave": 16,
      "tasks": ["4"]
    }
  ]
}
```

Tasks 1 and 2 are standalone pre-fix evidence and must complete in order. Each implementation subtask depends on the preceding subtask because later layers consume the contracts, metadata, storage, and runtime assembled earlier. Tasks 3.12 and 3.13 re-run the same properties from tasks 1 and 2; task 4 depends on every prior task.

## Notes

- Follow the test-first discipline: establish failing bug-condition evidence and passing preservation baselines before the fix, then re-run the same tests as fix and preservation checks.
- Use deterministic fakes only; do not require live services, development servers, or watchers.
- This bugfix does not implement frontend code or add `apps/web`; its scope is limited to frontend-facing backend structure and contracts.
- Keep every implementation and validation task traceable to the cited requirement clauses and design specifications.

## Tasks

- [ ] 1. Write the frontend/backend boundary bug-condition exploration tests
  - **Property 1: Bug Condition** - Milestone 4 Frontend-Facing Boundary Defects
  - **CRITICAL**: Write and run these tests against the unfixed code before changing any production module. They encode `expectedBehavior(input)` from the design and therefore MUST FAIL on the current implementation; do not weaken assertions or fix production code in this task.
  - Add `tests/test_milestone_4_structure_bug_exploration.py` for stable cross-component scenarios and extend the existing focused files named below where their fakes already model the relevant boundary. Use fixed seeds, bounded generated domains, fake clocks/pools/connections, `asyncio.Event` barriers, fakeredis, direct Celery task invocation, `TestClient`, and generated OpenAPI only.
  - In `tests/test_watch_service_history_wiring.py`, block the current history recorder behind an event and assert creation plus zero-delay dispatch, committed poll results, cancellation, and legacy transitions complete before the writer is released. Generate bursts larger than a small capacity and assert the future handoff never blocks or grows beyond that capacity while failures remain contained.
  - In `tests/test_monitor_watch.py`, exercise production-shaped `build_watch_service()` composition with cached factories replaced by deterministic doubles; assert a worker commit offers the same projection contract as API-local execution without changing the worker result dictionary or retry classification. Record the current counterexample that worker construction has no history collaborator.
  - In `tests/test_watch_owner_scoping.py` and repository tests, fail the first owner projection, permit a later poll/cancel projection, and assert the accepted owner is recovered from private live metadata. Exercise both `InMemoryWatchRepository` and `RedisWatchRepository`; record that the current runtime/result paths lose the owner.
  - In `tests/test_watch_history.py`, generate source revisions, duplicates, and completion permutations for one watch. Assert persisted public state is the greatest revision, equal revisions are idempotent, a null owner may be filled, and a conflicting non-null owner cannot reassign the row. Record the current last-completion-wins and owner-reassignment counterexamples.
  - Through `GET /api/watches/mine`, seed stale history plus newer retained live state, live-only state, history-only terminal state, two owners, divergent creation/update order, equal creation timestamps, and 235 watches. Assert live wins by `watch_id`, isolation holds, traversal is `(created_at DESC, watch_id DESC)`, and bounded cursor pages reach every stable item exactly once; record the current stale, `updated_at`-ordered, bare-list, and 100-row truncation behavior.
  - Parameterize omitted, trimmed-valid, and present-malformed `X-Dibs-Client-Id` values across `POST /api/watches`, `POST /api/parse-and-book`, and `/mine`. Assert malformed input returns the stable 422 error before parsing, creating, dispatching, or reading history, while omission remains distinct. Record the current successful unowned-creation counterexample.
  - Compare valid-owner `/mine` calls for available-empty history, omitted PostgreSQL, startup-disabled history, and a read exception. Assert only a successful available read returns an empty page, unavailable reads return the sanitized 503 contract, and the observed failure degrades history readiness; record the current `200 []`/uncontrolled-error ambiguity.
  - Poison `app.state.watch_history` while supplying a substitutable owned-watch query fake and assert the route uses only the application dependency. Generate OpenAPI and assert the documented request, 200 page, 422 errors, 503 error, unchanged `Watch`, and unchanged legacy endpoint schemas; record the current concrete app-state coupling and missing contract check.
  - In `tests/test_cors.py`, `tests/test_postgres_config.py`, `tests/test_main_postgres_wiring.py`, and `tests/test_postgres_migrations.py`, cover the omitted/valid/invalid capability matrix; user information, query, fragment, wildcard, non-root path, missing host, malformed/out-of-range port, and root-slash origins; unreachable PostgreSQL; every migration/schema verification failure phase; empty/missing packaged migrations; and failures after pool creation. Assert configured defects abort startup, resources close exactly once, and omission remains standalone.
  - Force PostgreSQL failures with unique sentinel username, password, and query values. Search exception text, captured logs, health JSON, and HTTP bodies and assert no sentinel appears while safe host/port/database context remains. Record the current raw-DSN leak.
  - Add a deterministic contract-drift probe that mutates an in-memory copy of the expected public schema and asserts the comparator rejects it; record that no current backend/future-frontend contract artifact detects the mutation.
  - Run the focused exploration files only. **EXPECTED PRE-FIX OUTCOME**: every defect-reproducing expected-behavior assertion fails for a concrete counterexample while unrelated test setup remains green. Record failing case IDs, minimal generated traces, observed statuses/shapes, occupancy, close counts, and leaked sentinels; if a hypothesized counterexample does not reproduce, reconcile the design before implementation.
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 1.11, 1.12, 1.13, 1.14, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.13, 2.14_

- [ ] 2. Write preservation property tests before implementing the fix
  - **Property 2: Preservation** - Milestone 1–3 and Completed Milestone 4 Behavior
  - **IMPORTANT**: Follow observation-first methodology. Run non-bug-condition cases on the unfixed code, record actual public results, state transitions, schedules, retries, readiness, and cleanup, then encode those observations and confirm this suite PASSES before changing production code.
  - Extend `tests/test_milestone_4_preservation_baseline.py` with bounded generated cases for headerless `POST /api/watches` and `POST /api/parse-and-book`; preserve status codes, complete response fields, all `PromptExecutionResult` statuses, `WATCH_CREATED.watch_id`, immediate first dispatch, and unowned visibility through the unscoped API.
  - Snapshot the exact public `Watch` field set, `WatchStatus` values, and `WatchPollOutcome` values. Assert owner, source revision, cursor, queue, and projection metadata never enter public `Watch` JSON, notification payloads, worker arguments, or logs.
  - Preserve unscoped `GET /api/watches`, `active_only`, by-id GET/DELETE status and body contracts, existing 404 details, and the fact that anonymous owner metadata is not an authorization boundary.
  - Extend `tests/test_watch_repository_oracle.py` and `tests/test_watch_repository_state_machine.py` so fixed-seed traces compare repository decisions, claims, fences, revisions, schedules, events, cleanup, and public state before/after a no-op or immediately failing projection collaborator. Assert PostgreSQL never participates in claim, lease, commit, dispatch, retry, or booking decisions.
  - Preserve successful live create/poll/cancel/expiry results after immediate projection exceptions, including notifications and successor scheduling, while excluding only newly backgrounded projection timing/log/readiness observations from differential equality.
  - Preserve omitted-PostgreSQL and omitted-CORS startup, all existing `/api/*` and `/health` behavior, non-browser requests, exact configured CORS methods/headers/exposed headers, no wildcard origins, and `allow_credentials=False`.
  - Preserve owner isolation and history-only terminal visibility after live retention cleanup for available history; test two or more opaque identifiers without treating them as authentication.
  - Preserve the exact `/health` key set and top-level `status` meaning; generated readiness transitions must remain within `ready`, `degraded`, and `unknown` and must not bleed across queue, recovery, and history signals.
  - Extend `tests/test_monitor_watch.py` with history-neutral variants of successful polls, the aliased Redis/Kombu recoverable tuple, non-recoverable failures, serialized runner access, retry limit/countdown, result keys, lazy resource construction, and repeated cleanup. All observations other than the new projection offer must equal the unfixed baseline.
  - Add static checks that the FastAPI backend has no import from `apps.web`/`apps/web`, remains independently packageable, and that tests use only deterministic fakes/fakeredis. Keep every existing Milestone 1–3 and completed Milestone 4 test enabled and unweakened.
  - Run the preservation files on the unfixed code. **EXPECTED PRE-FIX OUTCOME**: all preservation assertions PASS and establish the baseline rerun in task 3.13.
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13_

- [ ] 3. Fix the Milestone 4 frontend/backend structure

  - [ ] 3.1 Introduce application-level history ports and public HTTP contracts
    - Create `backend/services/watch_history.py` as the application-owned boundary. Define immutable `ProjectionEnvelope(watch, owner_client_id, source_revision)`, `ProjectionOffer` (`ACCEPTED`, `COALESCED`, `REJECTED_FULL`, `REJECTED_CLOSED`), `WriteDisposition`, `ProjectionPublisher`, `HistoryWriter`, `HistoryReader`, `OwnedLiveWatchReader`, `OwnedWatchQuery`, sorted page/key types, and typed `HistoryUnavailable`/`InvalidCursor` errors.
    - Keep this module free of imports from `backend.db`, FastAPI, asyncpg, Redis, Celery, or process-local `app.state`. Supply an exception-contained `NoopProjectionPublisher` for standalone live operations and an explicit unavailable history reader/query; do not represent unavailable history as `None` or an empty result.
    - Create `backend/api/contracts.py` with `PublicError`, `PublicErrorBody`, `OwnedWatchItem`, `OwnedWatchPage`, page metadata, and `LIVE`/`HISTORY_ONLY` tracking state. Keep the embedded `Watch` unchanged and add stable OpenAPI examples for `INVALID_CLIENT_ID`, `INVALID_PAGINATION`, and `HISTORY_UNAVAILABLE`.
    - Add `tests/test_watch_history_application.py` and focused schema tests proving structural substitution with fakes, dependency direction, immutable/internal metadata, error sanitization, and unchanged `Watch` serialization before connecting adapters.
    - Focused validation: `python -m pytest tests/test_watch_history_application.py tests/test_watch_api.py tests/test_api.py` and `python -m mypy backend/services/watch_history.py backend/api/contracts.py`.
    - _Bug_Condition: `isBugCondition(input)` where lifecycle or HTTP code depends on PostgreSQL-shaped protocols, concrete process state, unavailable-as-empty behavior, or an undocumented frontend contract_
    - _Expected_Behavior: `expectedBehavior(input, result)` routes projection and owner queries through substitutable application contracts and exposes stable request/success/page/error schemas_
    - _Preservation: Public `Watch`, legacy route bodies, live repository authority, standalone live operations, and independent backend deployment remain unchanged_
    - _Requirements: 2.1, 2.2, 2.7, 2.9, 2.14, 3.3, 3.5, 3.7, 3.12, 3.13_

  - [ ] 3.2 Make client identity validation explicit and side-effect free
    - Refactor `backend/api/client_identity.py` so omission returns the anonymous state, a trimmed value matching `[A-Za-z0-9_-]{1,200}` returns the validated identifier, and any present malformed value raises a typed boundary error. Never log or echo the rejected token.
    - In `backend/api/routes/watches.py` and the `POST /api/parse-and-book` route in `backend/main.py`, validate the header before date/orchestrator/service/history side effects. Map malformed values to the exact 422 `PublicError` contract; omission must still execute the existing unowned creation flow.
    - Keep `WatchService.create(..., owner_client_id=None)` source-compatible for scripts/direct callers. Do not add identity parameters to list/read/cancel-by-id or treat the owner as authorization.
    - Replace the existing defect-expecting identity assertions in `tests/test_watch_owner_scoping.py` with task 1's unchanged expected-behavior assertions; cover valid boundaries, trimming, malformed partitions, both creation paths, `/mine`, and zero downstream calls.
    - Focused validation: `python -m pytest tests/test_watch_owner_scoping.py tests/test_watch_api.py tests/test_api.py tests/test_milestone_4_preservation_baseline.py`.
    - _Bug_Condition: `isBugCondition(input)` where `X-Dibs-Client-Id` is present and malformed but collapses to anonymous_
    - _Expected_Behavior: `expectedBehavior(input, result)` returns sanitized `INVALID_CLIENT_ID` with no parse/create/dispatch/history side effect while preserving omission as valid anonymous input_
    - _Preservation: Headerless creation, valid trimming/grammar, parse-and-book statuses, unscoped/by-id routes, and non-authorizing ownership semantics remain unchanged_
    - _Requirements: 2.3, 2.6, 2.9, 3.1, 3.2, 3.3, 3.4_

  - [ ] 3.3 Retain owner and revision metadata in the in-memory live path
    - Add bounded optional `owner_client_id` to the private `WatchRuntime` schema in `backend/models/watch_runtime.py`; initialize it atomically with revision 0, preserve it through `model_copy` transitions, and default legacy/pre-owner runtimes to `None` without reopening terminal state or changing policy fields.
    - Add an immutable private projection metadata value to successful `CreateResult`, `CommitResult`, and `TransitionResult` in `backend/db/repositories/watch_decisions.py`. Populate it only from the committed runtime's retained owner and authoritative revision; no-op/fenced/unknown operations must not manufacture newer projection facts.
    - Extend `WatchRepository`/`InMemoryWatchRepository` with the `OwnedLiveWatchReader` page contract and a private owner index maintained under the existing lock during create, transition retention, delete, and cleanup. Sort exclusively by `(created_at DESC, watch_id DESC)` and apply an exclusive keyset boundary.
    - Update legacy `save`/sidecar migration paths so later projected transitions retain an owner when one exists and old records remain unowned. Keep owner and revision out of `Watch`, notification text, scheduling arguments, logs, and public repository list methods.
    - Extend `tests/test_watch_repository_state_machine.py` for create/poll/cancel/expire/cleanup metadata, failed-first-projection owner recovery, legacy defaults, and exact public JSON; validate owner page ordering and isolation with equal timestamps and bounded pages.
    - Focused validation: `python -m pytest tests/test_watch_repository_state_machine.py tests/test_watch_service.py tests/test_watch_api.py`.
    - _Bug_Condition: `isBugCondition(input)` where the first owner projection fails or a later transition lacks the owner/revision committed with live state_
    - _Expected_Behavior: `expectedBehavior(input, result)` retains owner privately and exposes the exact committed source revision for every successful live transition_
    - _Preservation: Existing in-memory claims, fences, statuses, schedules, cleanup, public list/read/delete behavior, and legacy records remain unchanged_
    - _Requirements: 2.3, 2.4, 2.5, 2.8, 3.3, 3.4, 3.5, 3.6, 3.9_

  - [ ] 3.4 Implement equivalent owner/revision behavior in Redis and Lua
    - Update `backend/db/repositories/watches.py` and the exact production scripts in `backend/db/repositories/watch_scripts.py` so create stores owner metadata inside the runtime, successful create/commit/cancel/expire responses carry the committed runtime/revision facts, and every existing compare-and-set decision remains unchanged.
    - Add a private Redis owner index keyed by validated owner. Update it atomically with watch/runtime creation and terminal cleanup; never put owner values in Celery arguments, public watch JSON, notification payloads, or log fields. Legacy records without owner metadata remain readable and unowned.
    - Implement Redis `OwnedLiveWatchReader` keyset pages using the immutable creation key and a stable watch-id tie-breaker. Bound reads, self-heal stale owner-index members consistently with existing all/active index behavior, and leave existing all/active/schedule/terminal key contracts intact.
    - Extend `tests/test_watch_repository_oracle.py` fixed-seed traces to compare owner, revision, page results, cleanup, and all existing decisions between memory and fakeredis/Lua. Add true concurrent barriers for owner-index creation/cleanup and stale transition attempts, with seed/trace output on divergence.
    - Focused validation: `python -m pytest tests/test_watch_repository_oracle.py tests/test_watch_repository_state_machine.py tests/test_watch_repository.py`.
    - _Bug_Condition: `isBugCondition(input)` where Redis-backed worker transitions omit owner/revision facts or owner-page indexes diverge from authoritative live state_
    - _Expected_Behavior: `expectedBehavior(input, result)` makes Redis and memory expose equivalent private metadata and deterministic owner pages without changing live decisions_
    - _Preservation: Lua claim/fence/commit/dispatch/recovery semantics, key compatibility, public models, retryable redis-py exceptions, and bounded cleanup remain unchanged_
    - _Requirements: 2.1, 2.3, 2.4, 2.5, 2.8, 3.3, 3.5, 3.9, 3.11, 3.13_

  - [ ] 3.5 Add the bounded non-blocking projection runtime and migrate `WatchService`
    - Implement a finite-capacity projector in `backend/services/watch_history.py` with a deque of watch IDs and map of newest pending envelopes. `offer` must be synchronous, constant-time, non-awaiting, exception-contained, and safe across the API/worker calling contexts.
    - Accept a new watch below capacity; replace one watch's pending envelope only for a newer revision; treat stale/equal offers as coalesced without increasing occupancy; return `REJECTED_FULL` for a new watch at capacity and `REJECTED_CLOSED` after close. Record structured non-secret readiness/log evidence for rejection.
    - Implement one consumer per process that removes one ID at a time, awaits `HistoryWriter.record`, records `APPLIED`/stale/duplicate/failure evidence, continues after write exceptions, stops acceptance on close, and drains only to a finite deadline. Prove occupancy never exceeds capacity with a blocked writer and generated bursts; use events/barriers rather than sleeps.
    - Remove `WatchHistoryRecorder` and all `backend.db` imports from `backend/services/watch_service.py`. Inject only `ProjectionPublisher`; construct envelopes from successful repository result metadata and call `offer` without `await` after preserving the existing live commit, notification, and dispatch ordering.
    - On creation, commit live state and the durable marker, perform the existing zero-delay dispatch, then offer revision 0 without waiting for PostgreSQL. On committed poll/cancel/expiry and legacy transitions, offer exactly the authoritative committed revision; fenced/no-op/unknown operations must not invent one. Offer rejection/failure cannot change returned `Watch`/`WatchPollResult`, dispatch, notification, or retry behavior.
    - Replace synchronous recorder assertions in `tests/test_watch_service_history_wiring.py` with envelope/offer assertions while retaining task 1's blocked-writer tests unchanged. Cover accepted, coalesced, full, closed, failed writer, duplicate delivery, outer cancellation, and legacy paths.
    - Focused validation: `python -m pytest tests/test_watch_history_application.py tests/test_watch_service_history_wiring.py tests/test_watch_service.py tests/test_invariants.py`.
    - _Bug_Condition: `isBugCondition(input)` where slow, blocked, failed, saturated, or distributed history I/O delays or alters a committed live operation_
    - _Expected_Behavior: `expectedBehavior(input, result)` offers owner-aware revisioned envelopes through a bounded non-blocking handoff and contains eventual failures to logs/readiness_
    - _Preservation: Live result/status/body, claim/fence/commit, first and successor dispatch, notification, projection-exception success, and worker retry decisions remain unchanged_
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.14, 3.5, 3.6, 3.11, 3.13_

  - [ ] 3.6 Add revision-guarded history storage, keyset reads, and packaged migrations
    - Add an ordered `0002_*.sql` migration without editing the applied `0001_watch_history.sql`. Backfill/add `source_revision BIGINT NOT NULL`, replace/add the owner-list index on `(owner_client_id, created_at DESC, watch_id DESC)`, and retain immutable `watch_id`/`created_at`.
    - Refactor `backend/db/repositories/watch_history.py` to implement the application `HistoryWriter`/`HistoryReader` ports and accept `ProjectionEnvelope`. Insert missing rows; update public state only for a greater source revision; make equal revisions idempotent with null-owner fill only; preserve the first non-null owner; report conflicting non-null owners as a sanitized invariant failure; and return a disposition for applied/stale/duplicate writes.
    - Replace `updated_at` ordering and repository-default truncation with bounded exclusive keyset reads ordered by creation time and watch ID. Return sorted page data needed by the application merge, not HTTP DTOs.
    - Turn `backend/db/migrations` into a package, discover SQL with `importlib.resources`, declare `*.sql` package data in `pyproject.toml`, require the known ordered migration set, reject empty/missing resources, and verify the required table, columns, and owner-created index before publishing history services. Do not depend on a source-tree `Path`.
    - Rewrite the fake SQL model in `tests/test_watch_history.py` to exercise revision permutations, duplicates, owner fill/conflict, exact full `Watch` round trips, created-at tie ordering, exclusive boundaries, and bounded limits. Extend `tests/test_postgres_migrations.py` and `tests/test_container_assets.py` to inspect a locally built wheel in a temporary directory with no dependency download and assert both migrations are discoverable from the artifact.
    - Focused validation: `python -m pytest tests/test_watch_history.py tests/test_postgres_migrations.py tests/test_container_assets.py`.
    - _Bug_Condition: `isBugCondition(input)` where projection writes complete out of order, owner history exceeds one page, migrations are missing, or installed artifacts omit SQL resources_
    - _Expected_Behavior: `expectedBehavior(input, result)` keeps the greatest authoritative revision, preserves ownership, provides deterministic keyset chunks, and refuses an unverified schema/resource set_
    - _Preservation: PostgreSQL remains a passive current-state projection; complete public Watch JSON, owner isolation, history-only terminal durability, migration ordering/locking, and already-applied `0001` remain intact_
    - _Requirements: 2.3, 2.4, 2.5, 2.8, 2.12, 2.14, 3.3, 3.5, 3.9, 3.13_

  - [ ] 3.7 Implement live/history reconciliation and complete keyset pagination
    - Implement the versioned URL-safe cursor codec and `OwnedWatchQuery` in `backend/services/watch_history.py`. Encode only the last emitted `(created_at, watch_id)` key, reject malformed/unsupported/out-of-bounds cursors as `InvalidCursor`, and enforce default limit 50 with maximum 100.
    - Require available history for any valid-owner query; a live-only subset must never masquerade as a complete dashboard. Read bounded sorted chunks from `OwnedLiveWatchReader` and `HistoryReader`, merge by watch ID, choose the live snapshot on every collision, label live-only/overlap items `LIVE`, and preserve history-only terminal items as `HISTORY_ONLY`.
    - Fetch until `limit + 1` unique IDs or both sources end, then derive `has_more` and `next_cursor`. On a stable dataset, traversal must return every owned watch exactly once in `(created_at DESC, watch_id DESC)` order; never use `updated_at` as a cursor or sort key.
    - Record successful reads as ready and unavailable/read failures as degraded through the existing last-observation readiness model. Translate all adapter exceptions to sanitized `HistoryUnavailable` at the application boundary without leaking owner tokens, cursor text, SQL, or driver messages.
    - Add `tests/test_owned_watch_query.py` with fixed-seed generated owner-partitioned live/history sets, overlaps, stale revisions, tie keys, history-only cleanup, page sizes, cursor traversals, source chunk boundaries, empty available history, unavailable/failing history, and two-owner isolation. Print the seed and source datasets on failure.
    - Focused validation: `python -m pytest tests/test_owned_watch_query.py tests/test_watch_history.py tests/test_watch_repository_state_machine.py tests/test_history_readiness.py`.
    - _Bug_Condition: `isBugCondition(input)` where live and history disagree, history is unavailable, ordering differs, or an owner has more than one bounded response_
    - _Expected_Behavior: `expectedBehavior(input, result)` returns live-authoritative, complete, isolated, deterministic pages and distinguishes unavailable history from empty history_
    - _Preservation: History-only terminal visibility, opaque owner isolation, immutable public Watch values, and the ready/degraded/unknown vocabulary remain unchanged_
    - _Requirements: 2.5, 2.7, 2.8, 2.9, 2.14, 3.3, 3.9, 3.10, 3.13_

  - [ ] 3.8 Route public HTTP through application dependencies and pin contract drift
    - Add `get_owned_watch_query` to `backend/api/dependencies.py`; it must return only the application use case and be overrideable in tests. Remove direct `request.app.state.watch_history` access and concrete repository knowledge from `backend/api/routes/watches.py`.
    - Change only `/api/watches/mine` to `OwnedWatchPage`, with validated `limit`/`cursor`, omitted-header empty page without a history read, valid-owner query execution, 422 `INVALID_CLIENT_ID`/`INVALID_PAGINATION`, and 503 `HISTORY_UNAVAILABLE`. Keep every other watch route's status, body, path, validation, and error details unchanged.
    - Extend `ReadinessTracker` to record read, write, and handoff-overflow evidence without changing queue/recovery state. Keep `/health` exactly at the existing eight keys, preserve top-level `status`, and constrain `history_readiness` to the existing vocabulary.
    - Add `tests/test_public_api_contract.py` to override only `get_owned_watch_query`, poison concrete app state, and assert route substitution, no persistence imports, every documented response/status/schema, complete `Watch` fields/enums, all parse-and-book result variants, and exact legacy endpoints.
    - Generate deterministic FastAPI OpenAPI into a committed language-neutral artifact at `contracts/milestone-4-openapi.json`; add a one-shot export/check script and tests that fail when generated paths, statuses, headers, DTOs, examples, enums, or the future frontend-consumed artifact drift. Prove the comparator rejects a mutated in-memory artifact and statically forbid backend source imports from `apps/web` when that application is later added.
    - Focused validation: `python -m pytest tests/test_public_api_contract.py tests/test_watch_owner_scoping.py tests/test_history_readiness.py tests/test_api.py tests/test_watch_api.py` followed by the contract check command added by this task.
    - _Bug_Condition: `isBugCondition(input)` where HTTP reaches persistence/process state directly, errors/pages are inferred from repository behavior, readiness stays stale, or the public contract drifts_
    - _Expected_Behavior: `expectedBehavior(input, result)` uses an overrideable application dependency, stable public schemas, evidence-based readiness, and a deterministic frontend-consumable contract check_
    - _Preservation: Existing parse-and-book/unscoped/by-id contracts, exact Watch/enums, health keys/top-level meaning, and HTTP-only frontend/backend independence remain unchanged_
    - _Requirements: 2.6, 2.7, 2.8, 2.9, 2.14, 3.1, 3.2, 3.3, 3.4, 3.10, 3.12, 3.13_

  - [ ] 3.9 Make configured CORS/PostgreSQL startup fail fast and secret safe
    - Harden `CorsSettings.from_environment()` in `backend/config.py`: accept only HTTP(S), non-empty host, valid optional port 1–65535, no username/password, wildcard, query, fragment, or non-root path; normalize an optional root slash away. Preserve comma-separated exact origins and omission as disabled.
    - Stop swallowing `ConfigurationError` in `_configure_cors`; an explicitly malformed `FRONTEND_ORIGINS` must abort `create_app()` before middleware construction. Keep valid methods `GET`/`POST`/`DELETE`, headers `Content-Type`/`X-Dibs-Client-Id`, current exposed monitoring headers, and `allow_credentials=False`.
    - Make `PostgresSettings` exclude the raw DSN from `repr` and diagnostics. Parse enough structure to derive a safe target containing only scheme class, host, valid port, and database name; reject explicitly malformed settings without echoing raw input.
    - In `backend/db/postgres.py`, normalize connection, package discovery, migration, and schema-verification failures into controlled sanitized `ConfigurationError` categories. Never embed raw driver text or retain a secret-bearing exception cause. Hold a local pool until all verification succeeds; close it exactly once on every failure after creation and publish nothing.
    - Add deterministic failure-phase tests for parse, pool creation, migration lock/DDL/SQL, resource discovery, schema verification, projector construction, and normal shutdown. Use close-count fakes and sentinel secrets; inspect exceptions, `repr`, logs, health, and HTTP output. Retain successful concurrent migration locking and standalone omission.
    - Replace defect-expecting tests in `tests/test_cors.py` and `tests/test_main_postgres_wiring.py`; extend `tests/test_postgres_config.py` and `tests/test_postgres_migrations.py` with total generated URL/DSN partitions and secret scans.
    - Focused validation: `python -m pytest tests/test_cors.py tests/test_postgres_config.py tests/test_postgres_migrations.py tests/test_main_postgres_wiring.py`.
    - _Bug_Condition: `isBugCondition(input)` where configured origins/PostgreSQL are invalid or unreachable, migration/schema/package initialization fails, a pool leaks, or a DSN secret reaches an observable surface_
    - _Expected_Behavior: `expectedBehavior(input, result)` fails configured startup with a sanitized error, closes owned resources exactly once, and exposes no credentials/sensitive query values_
    - _Preservation: Omitted capabilities remain supported standalone; valid exact-origin CORS, migration lock/order, core routes, and non-browser behavior remain unchanged_
    - _Requirements: 2.10, 2.11, 2.12, 2.13, 2.14, 3.7, 3.8, 3.10, 3.13_

  - [ ] 3.10 Compose one verified history runtime in the API process
    - Add a shared infrastructure composition factory (for example `backend/db/history_runtime.py`) that consumes validated PostgreSQL settings/readiness and returns one owned runtime containing application publisher/query ports plus private writer/pool/projector resources. Both API and worker must call this factory rather than rebuilding partial variants.
    - In standalone mode, return `NoopProjectionPublisher` for live writes and an explicit unavailable owner query. In configured mode, create the pool locally, discover/apply/verify migrations, construct the revisioned repository and bounded projector, then publish the application ports only after every step succeeds.
    - Refactor `backend/main.py` lifespan/`_attach_postgres` and `_build_watch_service` so in-memory and Redis service rebuilds always receive the same application publisher, routes obtain the composed query through dependencies, and no concrete history repository is exposed as a route contract.
    - Start the API projector before request serving. On shutdown, stop acceptance, perform only the bounded drain, close projector resources, then close the verified pool exactly once. Make startup rollback idempotent and leave no partially published `app.state` history service.
    - Preserve existing Redis fallback/selection, recovery startup, queue selection, OpenAI/settings behavior, and health top-level status. A configured PostgreSQL failure now aborts lifespan; omission still starts all core routes.
    - Extend `tests/test_main_postgres_wiring.py` with standalone, configured-success, each configured-failure phase, service-rebuild publisher identity, bounded shutdown, and exactly-once close assertions using fakes only.
    - Focused validation: `python -m pytest tests/test_main_postgres_wiring.py tests/test_watch_recovery_wiring.py tests/test_api.py tests/test_watch_api.py tests/test_history_readiness.py`.
    - _Bug_Condition: `isBugCondition(input)` where API startup publishes partial persistence state, silently disables configured history, or lifecycle service instances receive inconsistent projection collaborators_
    - _Expected_Behavior: `expectedBehavior(input, result)` publishes one verified application runtime, fails configured initialization, and keeps live operation paths independent of PostgreSQL waits_
    - _Preservation: Standalone startup, Redis/memory and Celery/asyncio selection, recovery, all core routes, health meaning, and idempotent shutdown remain unchanged_
    - _Requirements: 2.1, 2.2, 2.7, 2.9, 2.10, 2.12, 2.13, 3.5, 3.7, 3.10, 3.13_

  - [ ] 3.11 Compose the same projection contract in each distributed worker process
    - Refactor `backend/workers/tasks/monitor_watch.py` so worker-process initialization calls the shared history-runtime factory and injects its `ProjectionPublisher` into `build_watch_service()`. Explicit configured-history initialization failures must fail worker-process startup rather than silently constructing an unprojected service.
    - Give the worker projector its own lifecycle/event loop thread so asynchronous PostgreSQL consumption continues after the serialized task runner returns; `monitor_watch` itself performs only the constant-time `offer` through `WatchService` and never awaits history or opens a pool per task.
    - Keep projection offer/write failures outside `_RECOVERABLE_INFRASTRUCTURE_ERRORS`; they are logged/readiness evidence only and cannot request a Celery retry. Preserve the exact success dictionary, aliased Redis/Kombu retry tuple, original exception identity, countdown 60, maximum retries, non-recoverable propagation, runner lock, and old one-argument/window-aware paths.
    - Extend worker cleanup to stop history acceptance, drain to a finite deadline, close history writer/pool, close Redis through the existing runner, and close the runner. Celery shutdown plus `atexit` must remain idempotent and construct no unused resources.
    - Extend `tests/test_monitor_watch.py` with production-shaped factory injection, API/worker envelope equivalence, blocked/failed writer, startup failure, consumer liveness after `Runner.run`, shutdown ordering/deadline, repeated signals, and all existing retry/result variants. Do not start Celery, Redis, PostgreSQL, or a broker.
    - Keep deployment changes scoped to this bugfix: do not add `apps/web` or the original plan's web compose service. Ensure the existing API and worker roles can receive the same optional PostgreSQL settings when deployment task 11 later wires environment values.
    - Focused validation: `python -m pytest tests/test_monitor_watch.py tests/test_task_queue.py tests/test_watch_runtime.py tests/test_container_assets.py`.
    - _Bug_Condition: `isBugCondition(input)` where a poll runs in the distributed worker but that process omits or cannot continue the shared history projection lifecycle_
    - _Expected_Behavior: `expectedBehavior(input, result)` offers the same revisioned projection in API and worker processes while preserving task results/retries and bounded idempotent cleanup_
    - _Preservation: Worker result shape, retry classification/limits, serialized runner access, Redis lifecycle, optional worker isolation, and backend deployability remain unchanged_
    - _Requirements: 2.1, 2.2, 2.10, 2.12, 2.13, 2.14, 3.5, 3.6, 3.11, 3.12, 3.13_

  - [ ] 3.12 Verify the bug-condition exploration tests now pass
    - **Property 1: Expected Behavior** - Milestone 4 Frontend-Facing Boundary Defects
    - **IMPORTANT**: Re-run the SAME assertions and generated scenarios from task 1; do not replace them with post-fix tests, relax bounds, remove counterexamples, or hide failures behind retries/skips.
    - Confirm API-local and worker transitions offer equivalent owner-aware revisions; blocked/full/failed projection cannot delay or alter live results; failed-first-write owner recovery works in memory and Redis; out-of-order writes never regress; live wins reconciliation; all pages are complete and stable; malformed identity has zero side effects; unavailable is distinct from empty; routes use application dependencies; configured startup fails safely; migrations/resources verify; secrets remain absent; and deliberate contract mutations fail the drift check.
    - Run focused suites in dependency order and preserve fixed seeds/counterexample output. **EXPECTED OUTCOME**: every task 1 test PASSES and each documented pre-fix counterexample is now a fix check.
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.13, 2.14_

  - [ ] 3.13 Verify all preservation property tests still pass
    - **Property 2: Preservation** - Milestone 1–3 and Completed Milestone 4 Behavior
    - **IMPORTANT**: Re-run the SAME observation-first tests from task 2 without weakening exact statuses, bodies, fields, enums, decisions, schedules, retries, health keys, CORS policy, owner isolation, cleanup, or external-service prohibitions.
    - Confirm header omission, all parse-and-book outcomes, unscoped/by-id routes, exact public Watch/status/outcome schemas, live state-machine authority, successful live results after projection failure, standalone startup, exact CORS, history-only terminal visibility, health semantics, worker retries/results/cleanup, and independent HTTP-only backend deployment remain equal to the unfixed baseline.
    - Re-run the existing repository oracle/state-machine suites and every existing Milestone 1–3/completed Milestone 4 test without rewriting assertions to accommodate this fix. **EXPECTED OUTCOME**: all task 2 and pre-existing tests PASS.
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13_

- [ ] 4. Checkpoint - complete deterministic validation and review the scope
  - Run the two workflow properties first: `python -m pytest tests/test_milestone_4_structure_bug_exploration.py tests/test_milestone_4_preservation_baseline.py`.
  - Run the application/live/history group: `python -m pytest tests/test_watch_history_application.py tests/test_watch_service_history_wiring.py tests/test_watch_repository_state_machine.py tests/test_watch_repository_oracle.py tests/test_watch_history.py tests/test_owned_watch_query.py tests/test_watch_owner_scoping.py tests/test_history_readiness.py`.
  - Run the HTTP/composition/infrastructure group: `python -m pytest tests/test_public_api_contract.py tests/test_watch_api.py tests/test_api.py tests/test_monitor_watch.py tests/test_main_postgres_wiring.py tests/test_postgres_config.py tests/test_postgres_migrations.py tests/test_cors.py tests/test_container_assets.py`.
  - Run the deterministic OpenAPI export/check command introduced in task 3.8 and locally inspect/build the backend wheel without dependency downloads; assert required SQL resources are present and no backend module imports frontend source.
  - Run the full suite once, without watch mode or external services: `python -m pytest`. Fix any regression caused by this bugfix, re-run the smallest failing group, then repeat the full suite.
  - Run `python -m mypy backend`, `python -m compileall backend tests`, and `git diff --check`. Resolve every diagnostic; do not suppress it or broaden ignores to make validation pass.
  - Review `git status --short` and the focused diff. Confirm no `apps/web` implementation was added, `Watch`/enums and Milestone 1–3 assertions were not weakened, `0001_watch_history.sql` was not rewritten, PostgreSQL never entered live correctness decisions, no raw secret or driver text is observable, and no test requires a live service.
  - Mark this checkpoint complete only when every exploration counterexample passes, every preservation baseline and existing test remains green, package/contract drift checks pass, all resources close within their bounds exactly once, and requirements 2.1–2.14 and 3.1–3.13 have direct test evidence. Ask the user if questions arise.
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.13, 2.14, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13_
