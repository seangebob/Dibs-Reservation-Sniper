> **SUPERSEDED — retired 2026-09-03. The unchecked boxes below are NOT pending work.**
> See [SUPERSEDED.md](SUPERSEDED.md) for what was verified real (and where it went),
> what is rejected because it would regress shipped behavior, and what is stale.

# Implementation Plan

## Overview

This plan implements the expanded complete-backend repair described by `bugfix.md` Requirements 1.1–1.33, Expected Behavior 2.1–2.33, Preservation 3.1–3.21, and design Properties 1–23. It retains the original Milestone 4 frontend/backend boundary work while adding the recovery, lifecycle, topology, validation, delivery, boundedness, migration, health, and deployment work now required by the design.

Execution remains test-first. Task 1 is the standalone pre-fix bug-condition property task; task 2 is the standalone observation-first preservation property task. No production implementation begins until both baselines are recorded. Task 3 is one ordered parent implementation task whose reviewable subtasks leave the repository testable after each step. Tasks 3.26 and 3.27 re-run the exact tests from tasks 1 and 2. Task 4 is the final deterministic checkpoint.

Use the repository's existing pytest convention for property testing: bounded finite-domain parameterization, fixed-seed generated traces, exact production Lua through the already pinned in-process fake, fake clocks, and controlled barriers. Do not add a property-testing dependency. No task may contact live PostgreSQL, Redis, Celery, a broker, OpenAI, a browser, or a provider; start a development server, worker, watcher, or interactive process; or implement frontend code. The only frontend-facing artifact in scope is a language-neutral HTTP/OpenAPI contract.

## Imported Sibling Guarantees

These are dependencies, not work to rewrite in this spec:

- `.kiro/specs/app-logging-startup-error-visibility`: keep `configure_application_logging()` as the first lifecycle action, preserve host-owned logging topology, and preserve the one visible retained `WatchSettings` error and its identity.
- `.kiro/specs/watch-route-worker-retry-hardening`: preserve retained-settings precedence, the missing-settings invariant, the exact aliased Redis/Kombu retry tuple, `countdown=60`, `max_retries=3`, traceback behavior, runner serialization, optional worker imports, success result shape, and lazy/idempotent cleanup. This plan may add independently owned resources but must not redefine those guarantees.
- `.kiro/specs/milestone-3-production-path-hardening`: preserve live repository authority, deadline policy, claims/fences, cadence windows, durable markers, dispatch generations, shared mock state, outage backoff, recovery baseline, readiness vocabulary, and terminal retention. This plan extends their atomic metadata, page, effect, and cleanup extension points; PostgreSQL and notification delivery never enter those live decisions.
- `specs/milestone-4-write-api-and-frontend/tasks.md` completed tasks 1–7: preserve the established public `Watch`, parse-and-book, CORS, standalone, and history intent contracts except where the expanded requirements explicitly add owner pages and stable error/readiness interfaces.

## Task Dependency Graph

```json
{
  "waves": [
    {"wave": 1, "tasks": ["1"]},
    {"wave": 2, "tasks": ["2"]},
    {"wave": 3, "tasks": ["3.1"]},
    {"wave": 4, "tasks": ["3.2"]},
    {"wave": 5, "tasks": ["3.3"]},
    {"wave": 6, "tasks": ["3.4"]},
    {"wave": 7, "tasks": ["3.5"]},
    {"wave": 8, "tasks": ["3.6"]},
    {"wave": 9, "tasks": ["3.7"]},
    {"wave": 10, "tasks": ["3.8"]},
    {"wave": 11, "tasks": ["3.9"]},
    {"wave": 12, "tasks": ["3.10"]},
    {"wave": 13, "tasks": ["3.11"]},
    {"wave": 14, "tasks": ["3.12"]},
    {"wave": 15, "tasks": ["3.13"]},
    {"wave": 16, "tasks": ["3.14"]},
    {"wave": 17, "tasks": ["3.15"]},
    {"wave": 18, "tasks": ["3.16"]},
    {"wave": 19, "tasks": ["3.17"]},
    {"wave": 20, "tasks": ["3.18"]},
    {"wave": 21, "tasks": ["3.19"]},
    {"wave": 22, "tasks": ["3.20"]},
    {"wave": 23, "tasks": ["3.21"]},
    {"wave": 24, "tasks": ["3.22"]},
    {"wave": 25, "tasks": ["3.23"]},
    {"wave": 26, "tasks": ["3.24"]},
    {"wave": 27, "tasks": ["3.25"]},
    {"wave": 28, "tasks": ["3.26"]},
    {"wave": 29, "tasks": ["3.27"]},
    {"wave": 30, "tasks": ["4"]}
  ]
}
```

Every graph node is a leaf checklist task; parent task 3 is intentionally not a graph node. Waves are strict prerequisites: complete each wave, including its focused one-shot validation, before starting the next. Tasks 1 and 2 establish the required pre-fix evidence; tasks 3.1–3.25 implement and integrate the design; tasks 3.26 and 3.27 re-run the same properties; task 4 depends on all previous waves.

## Design Property Coverage

| Design properties | Primary tasks |
|---|---|
| 1, 2 | 1, 2, 3.26, 3.27 |
| 3, 4, 5 | 3.1–3.2, 3.4–3.12, 3.21–3.22 |
| 6, 11, 23 | 3.16–3.18 |
| 7, 8, 9 | 3.11, 3.13–3.15, 3.21–3.23 |
| 10, 22 | 3.3 |
| 12 | 3.5–3.7, 3.19–3.20 |
| 13 | 3.12 |
| 14 | 3.23 |
| 15 | 3.25 |
| 16 | 3.24 |
| 17–21 | 2, the relevant implementation regression tests, and 3.27 |

## Tasks

- [ ] 1. Write the complete-backend bug-condition exploration property tests
  - **Property 1: Bug Condition** - Complete Backend Correctness
  - **CRITICAL**: Write and run all exploration tests against the unfixed code before changing any production module. Confirmed expected-behavior assertions MUST FAIL on concrete counterexamples; do not weaken them, fix production code, or reinterpret a failure as a test defect without reconciling the design evidence.
  - Create `tests/test_milestone_4_structure_bug_exploration.py` as the deterministic manifest for every confirmed intentionally failing case and `tests/test_backend_exploration_gates.py` for Requirement 1.33. Keep all red pre-fix assertions in these two dedicated modules; shared repository/service/API test files and the task 2 command must remain green. Reuse existing fakes through helpers or direct production-boundary calls, not by adding red tests to shared files. Use fixed seeds, bounded traces, fake clocks/pools/connections, `asyncio.Event` or thread barriers, direct task `.run`, FastAPI `TestClient`, exact production Lua through fakeredis, static package/container parsing, and socket denial.
  - Reproduce the original boundary defects in 1.1–1.14: API/worker projection divergence, blocked or saturated history latency, failed-first-write ownership loss, revision regression, stale history precedence, malformed identity collapse, unavailable-as-empty history, truncation/order, persistence coupling, configured CORS/PostgreSQL fail-open behavior, migration cleanup/package gaps, secret-bearing PostgreSQL diagnostics, and missing HTTP/OpenAPI drift evidence.
  - Reproduce 1.15–1.20 with focused recovery, startup, lifecycle, topology, worker cleanup, and redaction tests: recovery expiry omits terminal effects; environment mutation changes request dependencies; each acquisition/close failure leaks or skips work; throwing/unsupported topology is accepted or differs by role; Redis initialized without the persistent runner remains open; and generated Redis/PostgreSQL sentinel secrets appear through at least one current representation or diagnostic surface.
  - Reproduce 1.21–1.25 with direct/orchestrated fake-clock cases, constructor/JSON/update mutations, closed-Sunday/calendar-boundary cases, rendered log sentinel scans, and notifier failure-point/redelivery traces. Record reversed-window, out-of-horizon, elapsed-same-minute, invalid reconstruction, ambiguous closure, privacy, and committed-terminal ambiguity counterexamples.
  - Reproduce 1.26–1.32 with instrumented large indexes and round-trip counters, legacy index fixtures, authority pauses across lease expiry, horizon-entry clocks, loop exceptions, every retention class, migration-set mutations, wheel/container inspection, role health matrices, and compose parsing. Assert finite per-pass budgets, authority, wake tolerance, backlog visibility, migration identity, semantic readiness, and role wiring; record where current code violates each assertion.
  - Add the three Requirement 1.33 exploration gates in `tests/test_backend_exploration_gates.py`: bounded admission bursts with fake paid-call counters; forced cancellation compare-and-set contention through every current attempt plus a controlled final read; and notifier failures before/after side effect, lost acknowledgement, lease expiry, and redelivery with and without idempotency. Each gate records seed, minimal trace, observations, and classification. A non-reproduced hypothesis remains unproven and MUST NOT cause authentication, 429, cancellation-status, retry, or delivery-policy changes.
  - Include production-shaped API/worker role-composition parity in the exploration harness, but do not start Celery or any service. Reset module globals, caches, environment snapshots, logging state, and fake time after every case.
  - Run this immutable exploration baseline command: `python -m pytest tests/test_milestone_4_structure_bug_exploration.py tests/test_backend_exploration_gates.py`. **EXPECTED PRE-FIX OUTCOME**: every confirmed defect family has at least one failing expected-behavior assertion and documented counterexample; exploration-only gates complete deterministically with a classification. Record this exact command, failing case IDs, and gate outcomes as the immutable task 3.26 rerun manifest.
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 1.11, 1.12, 1.13, 1.14, 1.15, 1.16, 1.17, 1.18, 1.19, 1.20, 1.21, 1.22, 1.23, 1.24, 1.25, 1.26, 1.27, 1.28, 1.29, 1.30, 1.31, 1.32, 1.33, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.13, 2.14, 2.15, 2.16, 2.17, 2.18, 2.19, 2.20, 2.21, 2.22, 2.23, 2.24, 2.25, 2.26, 2.27, 2.28, 2.29, 2.30, 2.31, 2.32, 2.33_

- [ ] 2. Write observation-first preservation property tests before implementing the fix
  - **Property 2: Preservation** - Existing Public, Coordination, and Provider Behavior
  - **IMPORTANT**: Run non-bug-condition cases on the unfixed code first, record actual HTTP, state-machine, queue, notification, lifecycle, diagnostic, package, and deployment observations, then encode those observations. This standalone suite MUST PASS before production changes begin.
  - Extend `tests/test_milestone_4_preservation_baseline.py` with bounded generated cases for headerless/unowned creation, every parse-and-book status and field, exact public `Watch`/status/outcome schemas, unscoped and by-ID routes, malformed-ID exclusions only where explicitly corrected, and the current no-authentication/opaque-scope contract.
  - Import, do not duplicate, Milestone 3 repository/worker oracles. Compare valid in-memory and Redis traces for claims, fences, revisions, schedules, terminal events, cleanup, cancellation, dispatch, retries, and successful live results when projection immediately fails. PostgreSQL/notifiers must remain passive and never decide a live result.
  - Preserve standalone startup, exact CORS behavior, owner isolation and history-only terminal visibility, every legacy `/health` field/top-level meaning/vocabulary, worker result/retry/runner/optional-import behavior, retained startup error identity/logging topology, and independently deployable HTTP-only backend behavior.
  - Generate valid temporal/model/calendar/provider cases and preserve acceptance, serialized names/meanings, deterministic slots/capacity/idempotency, existing holiday outputs, provider error normalization, prompt separation, and injected-double behavior.
  - Preserve successful terminal delivery deduplication for repeated physical delivery of the same event. Keep owner/watch identity, source revision, projection state, reservation payload, claim token, mutable delivery state, credentials, and raw errors absent from public JSON, worker arguments, and logs. The only terminal-delivery log fields permitted are the approved opaque event correlation, event type, and controlled outcome category; sentinel tests freeze that allowlist.
  - Add a socket-denial fixture and static import checks proving the suite neither contacts external services nor imports backend Python from a future frontend. Keep all existing Milestone 1–4 and sibling tests enabled and unweakened.
  - Run this immutable preservation baseline command on unfixed code: `python -m pytest tests/test_milestone_4_preservation_baseline.py tests/test_api.py tests/test_watch_api.py tests/test_watch_service.py tests/test_watch_repository_state_machine.py tests/test_watch_repository_oracle.py tests/test_watch_policy.py tests/test_watch_runtime.py tests/test_watch_dispatcher.py tests/test_watch_recovery.py tests/test_watch_recovery_wiring.py tests/test_scheduler.py tests/test_monitor_watch.py tests/test_task_queue.py tests/test_config.py tests/test_cors.py tests/test_readiness.py tests/test_history_readiness.py tests/test_mock_booking.py tests/test_mock_booking_state.py tests/test_booking_service.py tests/test_providers.py tests/test_venues.py tests/test_logging_config.py tests/test_container_assets.py`. **EXPECTED PRE-FIX OUTCOME**: every assertion PASSES. Record this exact command and results as the immutable task 3.27 rerun manifest.
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13, 3.14, 3.15, 3.16, 3.17, 3.18, 3.19, 3.20, 3.21_

- [ ] 3. Implement the expanded complete-backend repair

  - [ ] 3.1 Introduce application-owned ports and explicit public DTO foundations
    - Affected files/components: new `backend/services/watch_history.py`, new focused application contract types under `backend/services/`, new `backend/api/contracts.py`, `backend/services/watch_service.py`, and contract tests.
    - Define immutable `CommittedFacts`, `ProjectionEnvelope`, offer/write dispositions, stable page/key types, `ProjectionPublisher`, history/live readers, `OwnedWatchQuery`, and typed unavailable/cursor errors in the application layer. Keep these modules free of imports from FastAPI, asyncpg, Redis, Celery, and concrete repositories.
    - Provide explicit no-op/unavailable implementations for supported standalone mode; never encode unavailable history as `None` or an empty collection.
    - Define `PublicError`, `OwnedWatchItem`, `PageInfo`, and `OwnedWatchPage` without changing embedded `Watch` fields or enums. Reserve controlled codes from the design and prohibit raw headers, cursors, URLs, driver messages, or reservation payloads in errors.
    - Add dependency-direction, immutability, substitution, serialization, and sanitized-error tests before connecting adapters. Import the sibling live-authority guarantees rather than moving repository decisions into these ports.
    - Design properties: 3, 4, 5, 17, 21.
    - Focused one-shot validation: `python -m pytest tests/test_watch_history_application.py tests/test_schemas.py tests/test_api.py`.
    - _Bug_Condition: `isBugCondition(input)` where application/HTTP code depends on PostgreSQL-shaped protocols, concrete process state, or undocumented repository output_
    - _Expected_Behavior: `expectedBehavior(input, result)` routes effects and owner reads through substitutable application contracts with stable public DTOs and explicit unavailable states_
    - _Preservation: Public `Watch`, legacy route bodies, live repository authority, standalone live operation, and HTTP-only frontend independence remain unchanged_
    - _Requirements: 2.1, 2.2, 2.7, 2.9, 3.3, 3.5, 3.7, 3.12, 3.13_

  - [ ] 3.2 Make client identity a three-state, side-effect-free boundary
    - Affected files/components: `backend/api/client_identity.py`, `backend/api/dependencies.py`, `backend/api/routes/watches.py`, the parse-and-book route in `backend/main.py`, and `tests/test_watch_owner_scoping.py`.
    - Distinguish omitted, trimmed-valid `[A-Za-z0-9_-]{1,200}`, and present-malformed identifiers. Raise a typed sanitized boundary error for malformed values without logging or echoing the token.
    - Resolve identity before parsing, temporal validation, service creation, dispatch, or history reads on creation, parse-and-book, and `/mine`. Map malformed input to the documented 422 error; keep omission as valid anonymous input.
    - Keep direct `WatchService.create(..., owner_client_id=None)` source-compatible and do not add ownership authorization to unscoped/by-ID operations.
    - Cover grammar boundaries, zero downstream calls, both creation paths, `/mine`, and exact omission behavior.
    - Design properties: 4, 17.
    - Focused one-shot validation: `python -m pytest tests/test_watch_owner_scoping.py tests/test_watch_api.py tests/test_api.py tests/test_milestone_4_preservation_baseline.py`.
    - _Bug_Condition: `isBugCondition(input)` where a present malformed client identifier collapses to anonymous and permits side effects_
    - _Expected_Behavior: `expectedBehavior(input, result)` returns a stable sanitized validation response before side effects while preserving omission as anonymous_
    - _Preservation: Headerless creation, valid identifiers, parse-and-book statuses, unscoped/by-ID routes, and non-authorizing opaque scope remain unchanged_
    - _Requirements: 2.3, 2.6, 2.9, 3.1, 3.2, 3.3, 3.4, 3.20_

  - [ ] 3.3 Share temporal/calendar policy and validate every model reconstruction/update path
    - Affected files/components: `backend/models/reservation.py`, `backend/models/watch.py`, `backend/models/watch_runtime.py`, orchestrator result schemas, `backend/orchestrator/validator.py`, `backend/orchestrator/router.py`, `backend/api/routes/watches.py`, `backend/data/venues.py`, `backend/integrations/mock_booking.py`, and focused policy/model/catalog tests.
    - Add one fakeable, zoned, second-precise reservation/calendar policy used by direct routes and orchestrated validation. Reject reversed windows, elapsed same-day instants, and dates beyond the rolling horizon through boundary-appropriate stable contracts while preserving orchestrator clarification semantics.
    - Replace ambiguous Sunday `None` behavior with distinct inherit and closed values. Generate complete holiday/sellout data for every accepted rolling-horizon year and preserve existing supported-year snapshots.
    - Enforce aware UTC/order/version/counter/transition/status-slot-booking invariants on constructors, JSON deserialization, persisted reconstruction, and updates. Replace unchecked transition-time `model_copy(update=...)` calls with validated evolvers that preserve immutable identity and monotonic revision.
    - Invalid persisted values fail closed for the bounded scanner to quarantine/prune and degrade readiness; valid existing values retain names, enums, and serialization.
    - Import the Milestone 3 deadline/outage policy rather than replacing it; this task only unifies validation and closes reconstruction gaps.
    - Design properties: 10, 17, 22.
    - Focused one-shot validation: `python -m pytest tests/test_validator.py tests/test_watch_policy.py tests/test_watch_runtime.py tests/test_invariants.py tests/test_schemas.py tests/test_venues.py tests/test_mock_booking.py`.
    - _Bug_Condition: `isBugCondition(input)` where direct/orchestrated policy differs, timestamps/state are invalid on reconstruction/update, or closure/calendar support is ambiguous_
    - _Expected_Behavior: `expectedBehavior(input, result)` applies one complete temporal/calendar decision and validates the fully merged model on every path_
    - _Preservation: Valid temporal/model values, public fields/enums, Milestone 3 deadline behavior, deterministic catalog slots, capacity, holidays, and provider contracts remain unchanged_
    - _Requirements: 2.21, 2.22, 2.23, 3.2, 3.3, 3.15, 3.16, 3.17, 3.21_

  - [ ] 3.4 Add runtime v3 owner/revision metadata, committed facts, and the terminal-event foundation
    - Affected files/components: `backend/models/watch_runtime.py`, a new focused private terminal-delivery model/contract module, `backend/db/repositories/watch_decisions.py`, `backend/db/repositories/watches.py`, `backend/db/repositories/watch_scripts.py`, and repository/delivery state-machine tests.
    - Migrate supported runtime v2 values explicitly to v3 with private bounded `owner_client_id`; legacy public-only records become v3 with `owner_client_id=None`; unknown future versions fail closed without reopening terminal state.
    - Define the private `TerminalEventRecord` and delivery-state vocabulary (`PENDING`, `IN_FLIGHT`, `RETRYABLE`, `DELIVERED`, `UNCERTAIN`, `EXHAUSTED`, and migration-only `LEGACY_SUPPRESSED`) with deterministic event identity, bounded payload, revision, attempts, claim/outcome fields, and finite retention deadlines. This task establishes the value/storage contract; task 3.16 adds claim and outcome transitions.
    - Return immutable `CommittedFacts` from successful create/commit/transition/eligible-expiry operations with the exact committed `Watch`, runtime, source revision, retained owner, and terminal event identity. Atomically create exactly one initial terminal-event record for `FOUND`, `BOOKED`, or `EXPIRED`; cancellation remains event-free; fenced/no-op/missing results must not invent facts or events.
    - Preserve the owner once non-null and validate every transition through task 3.3 evolvers. Keep owner/watch identity, revision, payload, claim token, and mutable delivery state out of public JSON, schedule arguments, worker results, and logs; logging may use only the approved opaque event correlation, event type, and controlled outcome category frozen by tasks 2 and 3.18.
    - Extend in-memory and exact-Lua state-machine/oracle tests before page/index tasks consume these records; do not alter sibling-owned claim/fence/decision enums.
    - Design properties: 3, 4, 6, 10, 11, 18, 23.
    - Focused one-shot validation: `python -m pytest tests/test_watch_runtime.py tests/test_terminal_delivery.py tests/test_watch_repository_state_machine.py tests/test_watch_repository_oracle.py tests/test_watch_service.py`.
    - _Bug_Condition: `isBugCondition(input)` where owner/revision/terminal identity is detached from the authoritative transition, a terminal event lacks a typed durable record, or invalid updates bypass validation_
    - _Expected_Behavior: `expectedBehavior(input, result)` exposes private facts and one initial terminal-event record from the same committed state while retaining owner and monotonic revision_
    - _Preservation: Public watch/outcome types and all Milestone 3 claims, fences, markers, dispatch generations, retries, successful-delivery deduplication, and terminal decisions remain unchanged_
    - _Requirements: 2.1, 2.3, 2.4, 2.15, 2.22, 2.25, 3.3, 3.5, 3.15, 3.18, 3.19_

  - [ ] 3.5 Add stable bounded live, owner, recovery, projection, and event pages in memory
    - Affected files/components: `backend/db/repositories/watches.py`, `backend/db/repositories/watch_decisions.py`, `backend/services/watch_recovery.py`, and new `tests/test_repository_bounded_scans.py`.
    - Define bounded `scan_watches_page`, owner-page, recovery-page, projection-page, terminal-event-page, and cleanup-page contracts with opaque exclusive continuations, immutable ordering keys, finite limits, and explicit `has_more`/backlog evidence.
    - Maintain ordered in-memory indexes under the repository lock for all/live owner/recovery/projection/event and due-cleanup classes. Do not construct whole due lists or copy whole dictionaries before slicing.
    - Keep legacy unscoped list semantics and JSON array shape, but make later HTTP encoding able to consume bounded pages. Self-heal stale index members only within the page budget.
    - Instrument calls, retained object counts, and continuation behavior over large generated datasets; every eligible stable item must remain reachable exactly once.
    - Design properties: 4, 12, 18.
    - Focused one-shot validation: `python -m pytest tests/test_repository_bounded_scans.py tests/test_watch_repository_state_machine.py tests/test_watch_recovery.py tests/test_watch_api.py`.
    - _Bug_Condition: `isBugCondition(input)` where live/owner/recovery/projection/event discovery reads or retains an entire growing collection before applying a nominal batch size_
    - _Expected_Behavior: `expectedBehavior(input, result)` bounds each page's reads/memory and preserves stable complete continuation_
    - _Preservation: Legacy unscoped ordering/body, owner isolation, state-machine decisions, and normal recovery outcomes remain unchanged_
    - _Requirements: 2.5, 2.8, 2.26, 3.4, 3.5, 3.9, 3.15, 3.18_

  - [ ] 3.6 Implement Redis ordered indexes, bounded bulk pages, and resumable legacy backfill
    - Affected files/components: `backend/db/repositories/watches.py`, `backend/db/repositories/watch_scripts.py`, `backend/db/repositories/watch_decisions.py`, configuration for finite page/backfill budgets, and Redis oracle/bounded-scan tests.
    - Add immutable-key sorted owner/recovery/projection/event/cleanup indexes and update them atomically with existing watch/runtime/schedule/terminal keys. Read at most one page with a fixed number of bounded pipeline calls; prohibit `SMEMBERS`, `KEYS`, and sequential unbounded per-record reads on these paths.
    - Dual-write additions/removals to legacy and ordered indexes after old-worker drain. Implement a leader-owned resumable `SSCAN COUNT` backfill with persisted epoch/cursor/examined/added/remaining state; normal scanners combine bounded ordered and legacy pages until completion and report migration backlog.
    - Convert legacy terminal IDs lacking payload/delivery evidence to finite-retention `LEGACY_SUPPRESSED` records without replay, preserving successful-delivery deduplication.
    - Extend fixed-seed in-memory/Redis equivalence traces, concurrent barriers, large-index round-trip budgets, interrupted/resumed backfill, mixed-index deduplication, and exact-script tests.
    - Design properties: 4, 6, 12, 18, 23.
    - Focused one-shot validation: `python -m pytest tests/test_watch_repository_oracle.py tests/test_watch_repository.py tests/test_repository_bounded_scans.py tests/test_watch_repository_state_machine.py`.
    - _Bug_Condition: `isBugCondition(input)` where Redis performs whole-set/sequential scans, lacks stable owner/effect indexes, or cannot upgrade legacy index state without gaps or replay_
    - _Expected_Behavior: `expectedBehavior(input, result)` provides bounded ordered pages and an idempotent resumable dual-write/backfill epoch with honest backlog_
    - _Preservation: Exact Milestone 3 Lua decisions/key compatibility, retryable redis-py errors, public models, old records, and memory/Redis behavioral equivalence remain unchanged_
    - _Requirements: 2.3, 2.4, 2.5, 2.8, 2.15, 2.26, 2.28, 3.3, 3.5, 3.9, 3.11, 3.15, 3.18, 3.19_

  - [ ] 3.7 Implement bounded non-blocking projection and live-state reconciliation
    - Affected files/components: `backend/services/watch_history.py`, `backend/services/watch_service.py`, readiness tracking, a shared history runtime factory, and projection tests.
    - Implement a finite map/deque publisher whose synchronous constant-time `offer` accepts, coalesces to the newest revision per watch, rejects a new watch at capacity, and rejects after close without awaiting or throwing into live operations.
    - Add one exception-isolated consumer per process with finite shutdown drain and controlled readiness/backlog evidence. Writer failure, overflow, closure, or cancellation must not alter committed live results, dispatch, or worker retry classification.
    - Add a bounded projection reconciler over task 3.5/3.6 pages so retained live snapshots repair dropped/failed envelopes. Track remaining backlog, oldest age, and loss beyond live retention instead of retaining live state forever.
    - Replace awaited DB-shaped history calls in `WatchService` with post-commit offers built only from `CommittedFacts`; preserve existing live operation ordering and terminal wake integration points.
    - Prove bounds with blocked writers and bursts larger than capacity, plus stale/equal/newer revisions, write failures, finite close, and API/worker thread-safe offers.
    - Design properties: 3, 12, 18.
    - Focused one-shot validation: `python -m pytest tests/test_watch_history_application.py tests/test_watch_service_history_wiring.py tests/test_repository_bounded_scans.py tests/test_watch_service.py tests/test_readiness.py`.
    - _Bug_Condition: `isBugCondition(input)` where projection can block, grow without bound, be omitted, or be lost without bounded repair_
    - _Expected_Behavior: `expectedBehavior(input, result)` offers newest owner-aware revisions without waiting and reconciles retained live state in finite pages_
    - _Preservation: Live status/result, claims/fences, first/successor dispatch, projection-failure success, and worker retry semantics remain unchanged_
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.26, 3.5, 3.6, 3.11, 3.13, 3.15_

  - [ ] 3.8 Make PostgreSQL history revision-monotonic and keyset-paged
    - Affected files/components: `backend/db/repositories/watch_history.py`, append-only `backend/db/migrations/0002_*.sql`, `backend/db/postgres.py`, and history/migration tests. Do not edit `0001_watch_history.sql`.
    - Add `source_revision`, immutable creation ordering, and `(owner_client_id, created_at DESC, watch_id DESC)` index support. Implement conditional writes: insert missing, update only a greater revision, equal revision idempotency with null-owner fill, first non-null owner preservation, and sanitized conflict rejection.
    - Replace `updated_at` ordering/default truncation with bounded exclusive keyset reads returning application page values rather than HTTP DTOs.
    - Preserve full public `Watch` round trips and history-only terminal rows; PostgreSQL remains a passive projection and never enters a live claim/commit transaction.
    - Cover completion permutations, duplicates, owner fill/conflict, equal timestamps, cursor boundaries, limits, and fake SQL failures without a live database.
    - Design properties: 4, 5, 13, 19.
    - Focused one-shot validation: `python -m pytest tests/test_watch_history.py tests/test_postgres_migrations.py tests/test_history_readiness.py`.
    - _Bug_Condition: `isBugCondition(input)` where history applies arrival order, can reassign owners, or silently truncates/update-sorts reads_
    - _Expected_Behavior: `expectedBehavior(input, result)` stores the greatest authoritative revision and exposes bounded deterministic creation-order pages_
    - _Preservation: Passive projection, public Watch values, owner isolation, history-only terminal durability, and applied `0001` remain unchanged_
    - _Requirements: 2.3, 2.4, 2.5, 2.8, 2.12, 3.3, 3.5, 3.9, 3.13_

  - [ ] 3.9 Implement live-authoritative owner reconciliation and complete pagination
    - Affected files/components: `backend/services/watch_history.py`, readiness integration, and new `tests/test_owned_watch_query.py`.
    - Implement a versioned integrity-checked URL-safe cursor over only `(created_at, watch_id)`, default limit 50, maximum 100, and stable sanitized invalid-cursor errors.
    - Require available history for valid-owner queries; bounded-merge live and history pages, deduplicate by watch ID, choose retained live state on overlap, and keep history-only terminal state. Fetch only finite overlap/lookahead and return every stable item exactly once.
    - Record successful reads as ready and disabled/failing reads as degraded without leaking owner, cursor, SQL, or exception text. Never return a live-only subset as a complete dashboard.
    - Generate owner partitions, overlaps, stale revisions, ties, cleanup, page sizes, source boundaries, insertion newer than a cursor, unavailable/empty/failing states, and complete traversal traces.
    - Design properties: 4, 19.
    - Focused one-shot validation: `python -m pytest tests/test_owned_watch_query.py tests/test_watch_history.py tests/test_watch_repository_state_machine.py tests/test_history_readiness.py`.
    - _Bug_Condition: `isBugCondition(input)` where stale history overrides live state, unavailable equals empty, or bounded responses cannot reach every owner record_
    - _Expected_Behavior: `expectedBehavior(input, result)` returns isolated live-authoritative, duplicate-free, complete keyset pages with explicit availability_
    - _Preservation: Opaque owner isolation, history-only terminal visibility, immutable public Watch values, and readiness vocabulary remain unchanged_
    - _Requirements: 2.5, 2.7, 2.8, 2.9, 3.3, 3.9, 3.10, 3.13_

  - [ ] 3.10 Route HTTP through application dependencies and pin the OpenAPI boundary
    - Affected files/components: `backend/api/dependencies.py`, `backend/api/routes/watches.py`, `backend/main.py`, `backend/api/contracts.py`, new `contracts/milestone-4-openapi.json`, a one-shot export/check script, and public contract tests.
    - Add overrideable application dependencies for lifecycle, owner query, and startup snapshot; remove concrete history/process-state access from routes.
    - Make only `/api/watches/mine` return the explicit page DTO with validated limit/cursor and stable 422/503 errors. Keep omitted identity as an empty page without a storage call and keep all legacy unscoped/by-ID shapes.
    - Route legacy `GET /api/watches` through `scan_watches_page` with a bounded page iterator and streaming JSON-array encoder; never materialize the complete repository result or all encoded items in server memory. Preserve the exact array body, status, order, `active_only` behavior, and established headers. Add an instrumented large-result test that bounds page size, repository reads, and peak retained server objects while decoding to the same client-visible JSON.
    - Map task 3.3 direct temporal violations to stable client-safe 422 codes while preserving orchestrator result meanings.
    - Generate/check deterministic OpenAPI for request, success, page, error, health, `Watch`, and parse-and-book schemas. Prove an in-memory mutation is rejected and statically forbid frontend imports of backend Python; do not create `apps/web`.
    - Design properties: 4, 5, 17, 19, 21.
    - Focused one-shot validation: `python -m pytest tests/test_public_api_contract.py tests/test_watch_owner_scoping.py tests/test_watch_api.py tests/test_repository_bounded_scans.py tests/test_api.py tests/test_schemas.py`.
    - _Bug_Condition: `isBugCondition(input)` where routes reach persistence/process state, repository shapes become HTTP, the legacy list accumulates an unbounded repository/encoded result, temporal errors differ, or the frontend contract can drift_
    - _Expected_Behavior: `expectedBehavior(input, result)` uses substitutable use cases, streams the legacy array from bounded pages, and exposes documented request/success/page/error schemas with deterministic drift detection_
    - _Preservation: Existing parse-and-book/unscoped/by-ID statuses, order, filters, headers, and body contracts; exact Watch/enums; no-auth semantics; and HTTP-only backend/frontend separation remain unchanged_
    - _Requirements: 2.6, 2.7, 2.8, 2.9, 2.14, 2.21, 2.26, 3.1, 3.2, 3.3, 3.4, 3.12, 3.20_

  - [ ] 3.11 Add secret-safe Redis/PostgreSQL values, diagnostics, and strict origin parsing
    - Affected files/components: `backend/config.py`, `backend/db/postgres.py`, Redis connection/composition helpers in `backend/main.py`, worker settings paths, `backend/logging_config.py` call sites only, and config/CORS/redaction tests.
    - Parse Redis/PostgreSQL URLs into secret-bearing value objects with `repr=False` and separate `SafeEndpoint` context containing only approved scheme class, host/socket class, valid port, and database name. `str`/`repr` of settings and snapshots must expose only safe context or `<configured>`.
    - Classify low-level parse/connect/migrate/cleanup errors into controlled codes, raise public/startup failures without raw causes, and construct logs/health/worker diagnostics only from safe fields. Preserve sibling-owned handlers, formatters, levels, and retained-error behavior.
    - Enforce complete HTTP(S) browser-origin grammar: host and valid optional port only, optional root slash normalization, no user info, wildcard, query, fragment, malformed port, or non-root path; omission remains disabled.
    - Search generated plain/encoded username, password, token, query, fragment, nested exception, and representation sentinels across exceptions, causes, logs, health, HTTP, and worker output.
    - Design properties: 5, 9, 19, 20.
    - Focused one-shot validation: `python -m pytest tests/test_config.py tests/test_postgres_config.py tests/test_cors.py tests/test_logging_config.py tests/test_main_postgres_wiring.py tests/test_monitor_watch.py`.
    - _Bug_Condition: `isBugCondition(input)` where connection secrets or invalid origins reach representations/diagnostics or configured origin support is accepted incorrectly_
    - _Expected_Behavior: `expectedBehavior(input, result)` retains only safe endpoint/category context and rejects every non-origin component with a sanitized startup error_
    - _Preservation: Valid exact-origin CORS, omitted capabilities, non-browser behavior, logging topology, and retained settings-error identity remain unchanged_
    - _Requirements: 2.10, 2.11, 2.13, 2.20, 3.7, 3.8, 3.10, 3.11, 3.14_

  - [ ] 3.12 Verify checksummed migration-set, applied-prefix, schema, and package identity
    - Affected files/components: `backend/db/postgres.py`, `backend/db/migrations/__init__.py`, append-only SQL resources, `pyproject.toml`, package metadata helpers, and migration/container tests.
    - Discover migrations with `importlib.resources`; require a non-empty known set of strictly increasing unique numeric versions and canonical names. Compute each SHA-256 and a canonical set identity including expected application schema version.
    - Upgrade version-only tracking under the migration lock only after known schema verification. Store immutable name/checksum/first-package provenance and singleton prefix/set/schema state; accept only a checksum-valid applied prefix plus append-only suffix.
    - Reject empty/missing/malformed/duplicate/reordered/changed/unknown/non-prefix sets, running-package metadata mismatch, rollback, and schema fingerprint mismatch with sanitized errors. Verify required tables, columns/types/nullability, constraints, and indexes before publishing history.
    - Explicitly package `*.sql`; inspect a locally built wheel/container context with no dependency downloads and verify resources, distribution version, checksums, and schema identity. Preserve concurrent migration locking and close ownership for task 3.14.
    - Design properties: 5, 9, 13, 21.
    - Focused one-shot validation: `python -m pytest tests/test_postgres_migrations.py tests/test_migration_identity.py tests/test_container_assets.py tests/test_watch_history.py`.
    - _Bug_Condition: `isBugCondition(input)` where migration discovery/history/schema/package identity is empty, duplicate, changed, unknown, or unverifiable_
    - _Expected_Behavior: `expectedBehavior(input, result)` accepts only a packaged non-empty ordered checksummed set whose applied history is a valid prefix and whose final schema matches_
    - _Preservation: Append-only `0001` history, valid suffix upgrades, migration locking, passive history semantics, and package version changes with identical bytes remain supported_
    - _Requirements: 2.12, 2.13, 2.29, 3.5, 3.7, 3.12, 3.13_

  - [ ] 3.13 Capture one immutable startup snapshot and make dependencies consistent
    - Affected files/components: `backend/config.py`, new `backend/services/startup.py`, `backend/main.py`, `backend/api/dependencies.py`, `backend/workers/celery_app.py`, `backend/workers/tasks/monitor_watch.py`, and snapshot tests.
    - Capture an immutable environment mapping once per app/worker bootstrap and retain one ready value or exact sanitized error for watch, application, PostgreSQL, CORS, role, and topology-related settings.
    - Make every request dependency, health path, composition factory, Celery configuration, and worker child use only the snapshot/serialized safe generation. Remove all request/task fallbacks to `from_environment()` or `os.getenv`.
    - Preserve the sibling first-call logging initializer and exact retained `WatchSettings` error object/log visibility. Celery object construction remains connection-free and optional worker imports remain lazy.
    - Mutate the process environment after capture and test ready/error identity, API/worker parity, concurrent reads, child generation mismatch, and no reparsing.
    - Design properties: 7, 20.
    - Focused one-shot validation: `python -m pytest tests/test_startup_snapshot.py tests/test_api.py tests/test_watch_api.py tests/test_monitor_watch.py tests/test_logging_config.py`.
    - _Bug_Condition: `isBugCondition(input)` where a request or worker reparses ambient environment and disagrees with startup composition/health or changes retained error identity_
    - _Expected_Behavior: `expectedBehavior(input, result)` resolves every consumer from one immutable value/error snapshot and generation_
    - _Preservation: Retained settings precedence/identity, startup logging topology, valid request behavior, worker optional imports, and Celery command/config meanings remain unchanged_
    - _Requirements: 2.16, 3.7, 3.10, 3.11, 3.14_

  - [ ] 3.14 Add transactional lifecycle ledger, startup rollback, and isolated exactly-once shutdown
    - Affected files/components: new `backend/services/lifecycle.py`, `backend/main.py`, shared history/effect runtimes, worker cleanup hooks, and lifecycle tests.
    - Register each acquired resource immediately with dependency phase and internal once guard before the next fallible step; publish one verified process runtime only after the complete graph succeeds.
    - On startup failure, close all acquired resources in safe reverse phase/LIFO order and re-raise the primary sanitized failure. On normal/repeated/concurrent shutdown, attempt every close exactly once even when individual callbacks throw.
    - Use phases to stop ingress/producers, recovery/dispatch, projection/delivery acceptance and finite drains, queues/notifiers/orchestrator, then PostgreSQL/Redis/loops. Cleanup diagnostics expose only resource kind, phase, and exception class.
    - Generate every acquisition subset, every primary failure position, one/many throwing closes, cancellation, repeated signals, concurrent close, publication visibility, and exact close counts.
    - Import sibling lazy cleanup semantics; empty caches must still construct nothing.
    - Design properties: 5, 7, 9, 20.
    - Focused one-shot validation: `python -m pytest tests/test_lifecycle_ledger.py tests/test_main_postgres_wiring.py tests/test_monitor_watch.py tests/test_api.py`.
    - _Bug_Condition: `isBugCondition(input)` where partial startup leaks resources or one cleanup failure skips later closes/replaces the primary failure_
    - _Expected_Behavior: `expectedBehavior(input, result)` rolls back or shuts down every acquired resource in safe order exactly once with isolated sanitized cleanup failures_
    - _Preservation: First logging action, successful startup order, lazy resource construction, retained primary error identity, and repeated shutdown idempotence remain unchanged_
    - _Requirements: 2.12, 2.17, 2.19, 2.20, 3.7, 3.11, 3.14_

  - [ ] 3.15 Apply one fail-closed Redis topology policy to API and worker roles
    - Affected files/components: new shared topology module, `backend/main.py`, `backend/workers/celery_app.py`, `backend/workers/tasks/monitor_watch.py`, Redis factories, readiness, and topology tests.
    - Return only `SUPPORTED_PRIMARY`, `UNAVAILABLE`, `UNSUPPORTED`, or `UNKNOWN` from a finite probe using safe endpoint context. Probe exceptions and ambiguous/cluster/sentinel topology never become supported.
    - Apply the design role matrix: API may use documented local fallback with honest degraded readiness; a distributed worker requiring Redis fails startup before consumer/repository binding on every non-supported decision. Publish and validate the topology generation in children.
    - Keep the exact sibling retry tuple and runtime retry behavior; topology is a startup/readiness decision, not a new Celery task retry.
    - Test supported/unavailable/unsupported/throwing probes, explicit versus omitted intent, API/worker parity, no atomic script use after failed gate, no raw URL diagnostics, and finite probe cleanup.
    - Design properties: 8, 9, 20.
    - Focused one-shot validation: `python -m pytest tests/test_redis_topology.py tests/test_main_watch_wiring.py tests/test_monitor_watch.py tests/test_readiness.py tests/test_postgres_config.py`.
    - _Bug_Condition: `isBugCondition(input)` where unknown/unsupported topology is treated as standalone or API and worker classify/bind it differently_
    - _Expected_Behavior: `expectedBehavior(input, result)` permits atomic repositories only under the common supported-primary decision and reports every other role outcome honestly_
    - _Preservation: API local fallback, exact worker retry/result behavior, optional imports, current Redis schemes intentionally supported, and live state-machine semantics remain unchanged_
    - _Requirements: 2.18, 2.19, 2.20, 3.5, 3.11, 3.14, 3.15_

  - [ ] 3.16 Implement token-fenced terminal delivery claims and outcomes
    - Affected files/components: the terminal-delivery contracts established in task 3.4, `backend/db/repositories/watch_decisions.py`, `backend/db/repositories/watches.py`, `backend/db/repositories/watch_scripts.py`, configuration bounds, and delivery state-machine tests.
    - Build on task 3.4's atomic `TerminalEventRecord` creation and tasks 3.5–3.6's bounded event pages/indexes; do not redefine those models, initial records, or page ownership.
    - Implement finite token-fenced claims and compare-owner transitions among `PENDING`, `IN_FLIGHT`, `RETRYABLE`, `DELIVERED`, `UNCERTAIN`, and `EXHAUSTED`, including lease recovery, retry ceilings, outcome categories, idempotent duplicate wakes, and immutable handling of `LEGACY_SUPPRESSED`.
    - Claim only from bounded due-event pages, preserve one event per terminal transition under concurrency/recovery, and keep every delivery field out of public `Watch` and worker results.
    - Generate claim races, stale tokens, expiry takeover, duplicate task delivery, definite/unknown outcomes, exhaustion, legacy suppression, and memory/Redis equivalence.
    - Design properties: 6, 11, 12, 18, 23.
    - Focused one-shot validation: `python -m pytest tests/test_terminal_delivery.py tests/test_watch_repository_state_machine.py tests/test_watch_repository_oracle.py tests/test_repository_bounded_scans.py`.
    - _Bug_Condition: `isBugCondition(input)` where a durable terminal record cannot represent a finite claim, retry, uncertainty, recovery, or completion outcome_
    - _Expected_Behavior: `expectedBehavior(input, result)` advances the existing idempotent event only through token-fenced finite transitions over bounded due pages_
    - _Preservation: Atomic terminal event identity, live terminal status/outcome, cancellation behavior, successful user-visible at-most-once delivery, public schemas, and Milestone 3 terminal authority remain unchanged_
    - _Requirements: 2.15, 2.25, 2.26, 2.28, 3.3, 3.5, 3.15, 3.18, 3.19_

  - [ ] 3.17 Route normal and recovery expiry through shared committed terminal effects
    - Affected files/components: new `backend/services/terminal_effects.py`, `backend/services/watch_service.py`, `backend/services/watch_recovery.py`, projection/delivery wake ports, and recovery/effect tests.
    - Implement a non-throwing `TerminalEffects.observe(CommittedFacts)` that offers projection and wakes an existing durable terminal event without waiting. Call it after successful normal terminal commits and recovery `expire_if_eligible` commits.
    - Repeated recovery/no-op outcomes must not manufacture facts, revisions, events, projections, or notifications; the bounded pending-event scanner recovers an already-created undelivered event independently.
    - Return live create/poll/cancel/recovery results truthfully when either effect path fails; record controlled backlog/readiness evidence instead of entering worker retry classification.
    - Differentially compare normal versus recovery expiry, projection revisions/owners, event identity, repeated recovery, failed/closed effect runtimes, and concurrent expiry attempts.
    - Design properties: 3, 6, 11, 18.
    - Focused one-shot validation: `python -m pytest tests/test_terminal_effects.py tests/test_watch_recovery.py tests/test_watch_service.py tests/test_watch_service_history_wiring.py tests/test_monitor_watch.py`.
    - _Bug_Condition: `isBugCondition(input)` where recovery expiry commits terminal live state without normal projection/delivery effects or post-commit effect failure changes the live result_
    - _Expected_Behavior: `expectedBehavior(input, result)` feeds normal and recovery terminal commits through the same idempotent non-blocking effect observer_
    - _Preservation: Live repository decisions, recovery counters, worker result/retry behavior, cancellation's no-notification rule, and projection-failure success remain unchanged_
    - _Requirements: 2.1, 2.2, 2.15, 2.25, 3.5, 3.6, 3.11, 3.15, 3.19_

  - [ ] 3.18 Make notification logging privacy-safe and notifier uncertainty recoverable
    - Affected files/components: `backend/services/notification_service.py`, terminal delivery/recovery services, readiness, configuration, and privacy/delivery tests.
    - Enforce one terminal-log allowlist: the approved opaque event correlation, approved event type, and controlled outcome category. Omit watch/client identity, source revision, claim token, mutable delivery state, venue, date, party size, credentials, query text, slot, booking, payload, and raw exception details from messages and structured extras; no other delivery metadata is permitted.
    - Replace throwing/no-result notification with `DELIVERED`, `DEFINITELY_NOT_DELIVERED`, or `UNKNOWN`; unclassified throws map to `UNKNOWN` without changing the committed live result.
    - Require an idempotency-capable transport plus receipt recovery, or an explicit compare-and-set operator resolution path, before enabling automatic replay after uncertainty. While `UNCERTAIN`, prohibit automatic replay, degrade readiness, and retain bounded actionable state; erase payload at finite exhaustion and retain only a finite safe tombstone.
    - Test pre-side-effect failure, post-side-effect failure, lost acknowledgement, receipt says sent/absent, manual resolution, lease expiry, deadline exhaustion, repeated redelivery, successful deduplication, and exact sensitive-sentinel absence.
    - Do not select a provider guarantee beyond the approved result of task 1/3.24.
    - Design properties: 11, 16, 23.
    - Focused one-shot validation: `python -m pytest tests/test_notification_privacy.py tests/test_terminal_delivery.py tests/test_terminal_effects.py tests/test_readiness.py tests/test_milestone_4_preservation_baseline.py`.
    - _Bug_Condition: `isBugCondition(input)` where logs expose reservation data or a notifier throw makes a committed terminal result ambiguous and later recovery impossible_
    - _Expected_Behavior: `expectedBehavior(input, result)` keeps logs sanitized and records explicit delivered/retryable/uncertain/exhausted outcomes with an approved recovery path_
    - _Preservation: Successful same-event delivery remains user-visible at most once, public Watch/results remain exact, and no unapproved authentication/retry/delivery guarantee is introduced_
    - _Requirements: 2.24, 2.25, 2.33, 3.3, 3.19, 3.20, 3.21_

  - [ ] 3.19 Add authority-token renewal/loss, deadline-based recovery wakeups, and honest readiness
    - Affected files/components: `backend/services/watch_recovery.py`, `backend/workers/dispatcher.py`, live repository authority scripts/decisions, `backend/services/readiness.py`, configuration, and authority/scheduler tests.
    - Add an injected-monotonic-clock `AuthorityGuard` with owner/epoch/token/expiry/renewal margin. Check the token atomically before every leader-only mutation and renew before each page or operation whose hard timeout plus margin cannot fit.
    - Stop before the next side effect on renewal failure, timeout, cancellation, or authority loss; stale completions cannot mutate. Keep per-window dispatch claims as the final logical duplicate fence and preserve the Milestone 3 uncertain-publication rule.
    - Compute wake deadlines from next marker horizon entry, renewal deadline, immediate continuation backlog, and finite fallback tolerance; marker commits may signal early but lost signals cannot exceed tolerance.
    - Degrade recovery readiness immediately on loop exceptions, stale evidence, authority loss, or incomplete required index migration; require a later complete pass to recover.
    - Pause repository/broker calls across lease expiry and use fake clocks for horizon entry, long pages, lost wake signals, loop failures, renewal, and token rejection.
    - Design properties: 8, 12, 14, 18, 19.
    - Focused one-shot validation: `python -m pytest tests/test_recovery_authority.py tests/test_watch_recovery.py tests/test_watch_dispatcher.py tests/test_readiness.py tests/test_repository_bounded_scans.py`.
    - _Bug_Condition: `isBugCondition(input)` where a pass outlives authority, fixed sleeps dispatch late, or a failed loop still appears ready_
    - _Expected_Behavior: `expectedBehavior(input, result)` renews or stops before authority loss, wakes by deadline within tolerance, and exposes semantic degradation until recovery succeeds_
    - _Preservation: Existing claims/fences, dispatch generations, queue publication uncertainty handling, recovery outcomes, and readiness vocabulary remain unchanged_
    - _Requirements: 2.26, 2.27, 2.31, 3.5, 3.10, 3.15, 3.18_

  - [ ] 3.20 Complete bounded retention, reconciliation-pin cleanup, and backlog accounting
    - Affected files/components: `backend/db/repositories/watches.py`, `backend/db/repositories/watch_scripts.py`, `backend/db/repositories/mock_booking.py`, `backend/db/repositories/mock_booking_scripts.py`, recovery/cleanup reporting, and retention tests.
    - Return per-class bounded reports for terminal documents, runtime/fence/claim/dispatch/index state, terminal events/payloads, projection/delivery reconciliation metadata, expired pins and pin-owner indexes, idle slots, booking confirmations, and tombstones.
    - For each class report `examined`, `removed`, `remaining_due`, and `oldest_due_age`, plus aggregate work consumed. Use ordered due indexes and limits; never fetch all expired pins or derive backlog through an unbounded scan.
    - Retain unresolved delivery data until its finite resolution/dead-letter deadline, then erase payload and eventually tombstone; remove terminal event IDs and every associated index. Keep native TTL only as a backstop.
    - Make repeated cleanup idempotent and resumable after partial failures. Generate mixed due/future classes, large pin sets, interrupted passes, stale indexes, exact memory/Redis/mock parity, and eventual drain oracles.
    - Import Milestone 3 retention durations/semantics rather than redefining ordinary terminal or mock booking behavior.
    - Design properties: 11, 12, 18, 22, 23.
    - Focused one-shot validation: `python -m pytest tests/test_retention_accounting.py tests/test_mock_booking_state.py tests/test_mock_booking.py tests/test_watch_repository_oracle.py tests/test_terminal_delivery.py`.
    - _Bug_Condition: `isBugCondition(input)` where event/pin/tombstone/booking/index state accumulates or cleanup/backlog calculation itself is unbounded_
    - _Expected_Behavior: `expectedBehavior(input, result)` removes every due associated class in finite idempotent pages and reports exact remaining backlog/age_
    - _Preservation: Existing retention boundaries, booking idempotency/atomic winner/capacity, terminal history visibility, and repository equivalence remain unchanged_
    - _Requirements: 2.26, 2.28, 3.5, 3.9, 3.15, 3.17, 3.18, 3.19_

  - [ ] 3.21 Compose one verified runtime transaction in the API role
    - Affected files/components: `backend/main.py`, shared process/history/effect runtime factories, `backend/api/dependencies.py`, startup snapshot, lifecycle ledger, topology policy, migrations, projection/delivery pumps, and API composition tests.
    - Build resources locally from the immutable snapshot; classify topology, open/register PostgreSQL, verify migration/schema/package identity, construct repositories/ports/pumps/recovery/queues/providers, start finite loops, then atomically publish one `ProcessRuntime`.
    - In omitted standalone mode publish no-op projection and explicit unavailable owner query. Explicitly configured PostgreSQL/history, migration, schema, and dependent runtime failures abort startup and ledger-roll back. Redis topology outcomes follow task 3.15's role matrix instead: the API uses the documented local fallback with degraded readiness for `UNAVAILABLE`, `UNSUPPORTED`, or `UNKNOWN`, while workers fail startup. Never publish partial `app.state` services.
    - Ensure rebuilt in-memory/Redis watch services receive identical application ports and terminal effects. Start pumps before serving; shutdown stops producers, performs finite drains, and closes every resource exactly once.
    - Preserve API queue/store fallback policy, core routes, parse/provider behavior, legacy `/health` meaning, and first logging action. Do not add frontend code.
    - Test the capability matrix and every failure position with close-count fakes, environment mutation, safe diagnostics, publisher/effect identity, bounded shutdown, and no live services.
    - Design properties: 3, 5, 7, 8, 9, 14, 19, 20.
    - Focused one-shot validation: `python -m pytest tests/test_main_postgres_wiring.py tests/test_main_watch_wiring.py tests/test_watch_recovery_wiring.py tests/test_api.py tests/test_watch_api.py tests/test_lifecycle_ledger.py`.
    - _Bug_Condition: `isBugCondition(input)` where API composition silently disables configured capabilities, publishes a partial graph, reparses settings, or wires inconsistent effect collaborators_
    - _Expected_Behavior: `expectedBehavior(input, result)` atomically publishes one snapshot-consistent verified runtime and rolls back every configured failure_
    - _Preservation: Supported standalone startup, Redis/memory and Celery/asyncio selection, core routes, live authority, legacy health, and independent backend deployment remain unchanged_
    - _Requirements: 2.1, 2.2, 2.7, 2.9, 2.10, 2.12, 2.13, 2.16, 2.17, 2.18, 2.20, 3.5, 3.7, 3.10, 3.12, 3.13, 3.14_

  - [ ] 3.22 Compose the same verified effects in workers and close Redis without runner dependence
    - Affected files/components: `backend/workers/celery_app.py`, `backend/workers/tasks/monitor_watch.py`, shared process/history/effect runtime factories, lifecycle ledger, and worker tests.
    - Create one import-time `WorkerBootstrap` from a frozen environment without connecting. Gate topology in the parent before consumer start; pass only a safe snapshot/topology generation to children and reject generation mismatch.
    - In each child use the shared projection, delivery, migration, and repository factories. A task performs only synchronous effect offers/wakes around the existing serialized runner path; projection/notification failures never enter the recoverable retry tuple.
    - Register projection runtime, notification runtime, Redis client, service, and runner independently. If Redis exists without the persistent runner, use a temporary finite cleanup runner solely to `aclose` Redis, then close it. Empty caches construct nothing; Celery and `atexit` signals remain repeated/concurrent no-ops after first close.
    - Preserve exact result keys, original recoverable exception identity, `countdown=60`, `max_retries=3`, non-recoverable propagation, runner lock, optional imports, old one-argument jobs, and no per-task pool construction.
    - Test every initialization subset, startup failure, task success/retry matrix, API/worker envelope/event parity, background consumer liveness, finite drain order, Redis-without-runner cleanup, and repeated signals.
    - Design properties: 3, 7, 8, 9, 20.
    - Focused one-shot validation: `python -m pytest tests/test_monitor_watch.py tests/test_task_queue.py tests/test_startup_snapshot.py tests/test_redis_topology.py tests/test_lifecycle_ledger.py`.
    - _Bug_Condition: `isBugCondition(input)` where worker composition omits common effects/topology/snapshot guarantees or Redis cleanup incorrectly depends on runner initialization_
    - _Expected_Behavior: `expectedBehavior(input, result)` composes the same verified ports in each worker child and independently closes every initialized resource exactly once_
    - _Preservation: Exact sibling worker results/retries/tracebacks/runner serialization/optional imports, live decisions, and backend deployability remain unchanged_
    - _Requirements: 2.1, 2.2, 2.10, 2.12, 2.16, 2.17, 2.18, 2.19, 2.20, 3.5, 3.6, 3.11, 3.12, 3.13, 3.14_

  - [ ] 3.23 Add role-specific liveness/readiness and PostgreSQL deployment wiring
    - Affected files/components: `backend/services/readiness.py`, API health routes in `backend/main.py`, new worker health evidence/probe module, `Dockerfile`, `infra/docker-compose.yml`, package metadata, and role-health/container tests.
    - Preserve exact legacy `GET /health` keys, HTTP 200, top-level meaning, and vocabulary. Add API `/health/live` for process responsiveness and `/health/ready` returning 503 when a capability required by the startup snapshot is unknown/degraded/stale.
    - Publish worker parent/child generation-bound health evidence atomically through owner-only directory/files with finite heartbeat age, safe fields, PID/start identity, broker/topology/history verification, child counts, and backlog state. Add a finite one-shot local probe with distinct live/ready modes; it must not contact API or dependencies.
    - Keep the image API default if desired, but override compose healthchecks per role. Pass the same sanitized `POSTGRES_URL`/history settings to API and worker and depend on healthy PostgreSQL when the app profile configures durable history; preserve default infra-only Redis/PostgreSQL services, ports, volumes, and worker command.
    - Test capability truth tables, exact legacy health snapshot, endpoint status matrix, fake evidence files/clocks/PIDs/generations/permissions, stale/malformed states, package commands, and static Docker/compose semantics without starting containers.
    - Design properties: 9, 14, 19, 20, 21.
    - Focused one-shot validation: `python -m pytest tests/test_role_health.py tests/test_readiness.py tests/test_history_readiness.py tests/test_container_assets.py tests/test_api.py`.
    - _Bug_Condition: `isBugCondition(input)` where liveness implies readiness, workers use API-only HTTP health, or configured history roles lack PostgreSQL settings/dependency_
    - _Expected_Behavior: `expectedBehavior(input, result)` exposes role-appropriate finite liveness/readiness and wires every configured history user to verified PostgreSQL_
    - _Preservation: Exact legacy `/health`, same API/worker image and Celery command, default infra-only profile, existing service definitions, and standalone mode remain unchanged_
    - _Requirements: 2.30, 2.31, 3.7, 3.10, 3.11, 3.12, 3.13_

  - [ ] 3.24 Enforce deterministic exploration gates before any policy change
    - Affected files/components: `tests/test_backend_exploration_gates.py`, deterministic admission/contention/notifier doubles, and gate result records only unless a separately approved requirement changes scope.
    - Re-run task 1's bounded admission burst and record admitted concurrency/work, fake paid-call count, queue occupancy, and current responses. No authentication, rate limit, or 429 behavior may change without a reproducible counterexample and separately approved public outcome.
    - Re-run forced cancellation contention through all bounded attempts, then perform a controlled authoritative read and classify missing, terminal, active contention, or corrupt. Keep 404 only for truly missing state; do not add 409/503 or change retry bounds without evidence and approval.
    - Re-run notifier failure-point/idempotency matrices and record the transport guarantee selected by evidence. Generic throws remain `UNCERTAIN`; no automatic replay guarantee is inferred.
    - If a gate contradicts the current design or requires a new public outcome, stop implementation and request requirements/design approval instead of changing production behavior. If no counterexample reproduces, retain current behavior and the regression gate.
    - Include API/worker role-composition ordering, finite budgets, cache/global reset, and socket denial in every gate.
    - Design properties: 16, 17, 20, 23.
    - Focused one-shot validation: `python -m pytest tests/test_backend_exploration_gates.py tests/test_watch_owner_scoping.py tests/test_watch_api.py tests/test_monitor_watch.py tests/test_terminal_delivery.py`.
    - _Bug_Condition: `isBugCondition(input)` where admission, CAS contention, or notifier guarantee remains an unclassified hypothesis_
    - _Expected_Behavior: `expectedBehavior(input, result)` records deterministic evidence and permits behavior change only after a reproducible counterexample plus approved outcome_
    - _Preservation: No-auth endpoints, opaque client scope, cancellation status mapping, worker retry bounds/classes, and successful delivery deduplication remain unchanged unless separately approved_
    - _Requirements: 2.33, 3.11, 3.19, 3.20, 3.21_

  - [ ] 3.25 Complete deterministic cross-module evidence and network denial
    - Affected files/components: all focused `tests/test_*` files added above, `tests/conftest.py`, OpenAPI/package/container contract checks, and no production behavior beyond fixes already specified.
    - Add an autouse opt-in socket/process denial fixture for this bugfix's focused suites; explicitly fail attempts to contact PostgreSQL, Redis, broker, API server, OpenAI, browser, or providers.
    - Cover every traceability row for confirmed Requirements 1.1–1.32 using deterministic fakes, fake wall/monotonic clocks, barriers, bounded generated traces, direct API/model/task calls, exact Lua, and static artifacts. Emit seed/trace/failure phase/page cursor/lease/delivery sequence on failure.
    - Run imported sibling suites unchanged and add assertions that no test is skipped merely because the new behavior is difficult to fake. Keep optional-worker skips only for environments intentionally lacking the worker extra.
    - Check deterministic OpenAPI mutation rejection, wheel migration resources/identity, Docker/compose role wiring, no frontend Python import, and no `apps/web` implementation.
    - Design properties: 5, 15, 20, 21.
    - Focused one-shot validation: `python -m pytest tests/test_milestone_4_structure_bug_exploration.py tests/test_milestone_4_preservation_baseline.py tests/test_backend_exploration_gates.py tests/test_public_api_contract.py tests/test_container_assets.py`.
    - _Bug_Condition: `isBugCondition(input)` where cross-module correctness is inferred only from line coverage or sequential happy paths_
    - _Expected_Behavior: `expectedBehavior(input, result)` supplies deterministic failure-point, concurrency, boundedness, package, contract, and role evidence for every confirmed scenario_
    - _Preservation: Existing tests remain enabled/unweakened, deterministic doubles stay injectable, and no live service or frontend implementation enters validation_
    - _Requirements: 2.14, 2.32, 3.11, 3.12, 3.13, 3.14, 3.15, 3.21_

  - [ ] 3.26 Verify the same complete-backend bug-condition property now passes
    - **Property 1: Expected Behavior** - Complete Backend Correctness
    - **IMPORTANT**: Re-run the SAME confirmed-scenario assertions, fixed seeds, bounds, and gate harnesses from task 1. Do not replace them with post-fix tests, remove counterexamples, relax budgets/timing, hide failures behind retries/skips, or reinterpret a non-reproduced hypothesis as approval.
    - Confirm Properties 3–16: common non-blocking projection; owner/revision/reconciliation/pages; configured startup/migrations; recovery terminal effects; immutable snapshots/lifecycle; fail-closed topology; secret/privacy redaction; temporal/model/calendar parity; durable delivery; bounded scans/authority/wake/retention; migration identity; role health; complete evidence; and exploration-before-policy-change.
    - Re-run task 1's immutable command verbatim: `python -m pytest tests/test_milestone_4_structure_bug_exploration.py tests/test_backend_exploration_gates.py`. **EXPECTED OUTCOME**: every confirmed task 1 assertion now PASSES; each exploration-only gate remains deterministic and preserves unapproved behavior.
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.13, 2.14, 2.15, 2.16, 2.17, 2.18, 2.19, 2.20, 2.21, 2.22, 2.23, 2.24, 2.25, 2.26, 2.27, 2.28, 2.29, 2.30, 2.31, 2.32, 2.33_

  - [ ] 3.27 Verify the same observation-first preservation property still passes
    - **Property 2: Preservation** - Existing Public, Coordination, and Provider Behavior
    - **IMPORTANT**: Re-run the SAME task 2 baselines without rewriting expectations after the fix. Only explicitly specified additive private metadata, owner-page/error contracts, and dedicated role health interfaces may differ.
    - Confirm Properties 17–23: exact public/no-auth behavior; Milestone 3 authority and memory/Redis equivalence; standalone/CORS/owner/legacy health; worker and sibling hardening; independent HTTP-only frontend boundary; valid model/catalog/provider behavior; and successful terminal delivery deduplication.
    - Re-run task 2's immutable command verbatim: `python -m pytest tests/test_milestone_4_preservation_baseline.py tests/test_api.py tests/test_watch_api.py tests/test_watch_service.py tests/test_watch_repository_state_machine.py tests/test_watch_repository_oracle.py tests/test_watch_policy.py tests/test_watch_runtime.py tests/test_watch_dispatcher.py tests/test_watch_recovery.py tests/test_watch_recovery_wiring.py tests/test_scheduler.py tests/test_monitor_watch.py tests/test_task_queue.py tests/test_config.py tests/test_cors.py tests/test_readiness.py tests/test_history_readiness.py tests/test_mock_booking.py tests/test_mock_booking_state.py tests/test_booking_service.py tests/test_providers.py tests/test_venues.py tests/test_logging_config.py tests/test_container_assets.py`. Then run every remaining existing Milestone 1–4 test without weakening assertions. **EXPECTED OUTCOME**: all task 2 and pre-existing preservation tests PASS.
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13, 3.14, 3.15, 3.16, 3.17, 3.18, 3.19, 3.20, 3.21_

- [ ] 4. Checkpoint - validate the complete expanded bugfix and review scope
  - Re-run task 1's immutable exploration command verbatim: `python -m pytest tests/test_milestone_4_structure_bug_exploration.py tests/test_backend_exploration_gates.py`.
  - Re-run task 2's immutable preservation command verbatim: `python -m pytest tests/test_milestone_4_preservation_baseline.py tests/test_api.py tests/test_watch_api.py tests/test_watch_service.py tests/test_watch_repository_state_machine.py tests/test_watch_repository_oracle.py tests/test_watch_policy.py tests/test_watch_runtime.py tests/test_watch_dispatcher.py tests/test_watch_recovery.py tests/test_watch_recovery_wiring.py tests/test_scheduler.py tests/test_monitor_watch.py tests/test_task_queue.py tests/test_config.py tests/test_cors.py tests/test_readiness.py tests/test_history_readiness.py tests/test_mock_booking.py tests/test_mock_booking_state.py tests/test_booking_service.py tests/test_providers.py tests/test_venues.py tests/test_logging_config.py tests/test_container_assets.py`.
  - Run the application/model/history group in one shot: `python -m pytest tests/test_watch_history_application.py tests/test_watch_service_history_wiring.py tests/test_watch_history.py tests/test_owned_watch_query.py tests/test_watch_owner_scoping.py tests/test_public_api_contract.py tests/test_validator.py tests/test_watch_runtime.py tests/test_invariants.py tests/test_venues.py`.
  - Run the authority/effects/boundedness group in one shot: `python -m pytest tests/test_watch_repository_state_machine.py tests/test_watch_repository_oracle.py tests/test_repository_bounded_scans.py tests/test_terminal_delivery.py tests/test_terminal_effects.py tests/test_notification_privacy.py tests/test_watch_recovery.py tests/test_recovery_authority.py tests/test_watch_dispatcher.py tests/test_retention_accounting.py tests/test_mock_booking_state.py`.
  - Run the startup/role/package group in one shot: `python -m pytest tests/test_startup_snapshot.py tests/test_lifecycle_ledger.py tests/test_redis_topology.py tests/test_main_postgres_wiring.py tests/test_main_watch_wiring.py tests/test_monitor_watch.py tests/test_postgres_config.py tests/test_postgres_migrations.py tests/test_migration_identity.py tests/test_cors.py tests/test_role_health.py tests/test_container_assets.py tests/test_readiness.py tests/test_history_readiness.py`.
  - Run the deterministic OpenAPI export/check and build/inspect the backend wheel without dependency downloads. Assert migration resources/checksums/schema/package identity and worker probe modules are present; no backend module imports frontend source; no `apps/web` implementation exists.
  - Run the full suite once without watch mode or external services: `python -m pytest`. Fix any regression caused by this bugfix, re-run the smallest affected group, then repeat the full suite.
  - Run `python -m mypy backend`, `python -m compileall backend tests`, and `git diff --check`. Resolve diagnostics rather than suppressing them or broadening ignores.
  - Review `git status --short` and the focused diff. Confirm sibling specs were imported rather than reimplemented; `0001_watch_history.sql` and public `Watch`/enums were not rewritten; PostgreSQL/notifiers never entered live decisions; no secret/reservation sentinel is observable; every scan/drain/cleanup has a finite bound; no live service/watcher/frontend was added; and exploration-only hypotheses caused no unapproved behavior change.
  - Mark this checkpoint complete only when tasks 3.26 and 3.27 pass unchanged, every design Property 1–23 and requirement 2.1–2.33/3.1–3.21 has direct deterministic evidence, role/package/contract checks pass, and every initialized resource closes exactly once within its deadline. Ask the user if questions arise.
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.13, 2.14, 2.15, 2.16, 2.17, 2.18, 2.19, 2.20, 2.21, 2.22, 2.23, 2.24, 2.25, 2.26, 2.27, 2.28, 2.29, 2.30, 2.31, 2.32, 2.33, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13, 3.14, 3.15, 3.16, 3.17, 3.18, 3.19, 3.20, 3.21_

## Notes

- Preserve the test-first red/fix-check discipline: record pre-fix bug-condition failures, then rerun the same exploration tests unchanged as the fix check.
- Keep the observation-first preservation baseline immutable and rerun it unchanged after the fix.
- Use deterministic validation only; do not contact or start live services.
- Import sibling-spec guarantees from their owning specs rather than duplicating or redefining them.
- Do not implement frontend code.
- Exploration gates must not change public behavior unless reproducible evidence supports the change and the resulting outcome is separately approved.