# Milestone 4 Frontend Backend Structure Fix Bugfix Design

## Overview

This bugfix repairs the frontend-facing backend seams introduced by completed tasks 1–7 of `specs/milestone-4-write-api-and-frontend/tasks.md`. There is no `apps/web` implementation yet, so this design does not redesign frontend components or modify production code. It defines the backend application and HTTP contracts that the future separate Next.js application will consume.

Repository inspection confirms that the live Milestone 3 watch state machine remains sound, but the new history/frontend seam is attached at the wrong layers and with the wrong execution semantics. `WatchService` imports a persistence-layer history protocol and awaits PostgreSQL-shaped work on create, poll, cancel, and legacy paths; `/api/watches/mine` reaches a concrete repository through `app.state`; the Celery worker constructs a different `WatchService` with no history recorder; anonymous ownership exists only as an argument to the first projection call; history upserts have no authoritative freshness guard; and startup treats explicitly broken PostgreSQL and CORS configuration as if the capability had simply been omitted.

The fix keeps Redis/in-memory watch state and its fenced single-flight protocol authoritative. It introduces application-level ports for non-blocking projection and owner-scoped queries, retains owner and revision metadata privately beside the authoritative live record, projects through a bounded asynchronous handoff in both API and worker processes, and reconciles durable history with live state before returning a documented paginated HTTP collection. Explicitly configured infrastructure becomes fail-fast and secret-safe; intentionally omitted PostgreSQL and CORS remain supported standalone modes.

The design follows four constraints:

1. PostgreSQL is a passive current-state projection, never a participant in a claim, lease, fence, commit, dispatch, retry, or booking decision.
2. No PostgreSQL wait occurs on a live watch result path; saturation and eventual write failure are observable but cannot change that result.
3. Persistence adapters and process state are confined to composition. HTTP routes depend on application use cases and public schemas.
4. Existing Milestone 1–3 behavior and completed Milestone 4 public contracts remain unchanged except where the validated bugfix requirements explicitly replace defective behavior.

## Glossary

- **Bug_Condition (C)**: A tasks 1–7 frontend/backend boundary scenario containing distributed execution, slow/failed/out-of-order history I/O, lost ownership, stale history, unavailable or paginated owner reads, malformed identity, explicit configuration or migration failure, secret exposure, or public-contract drift.
- **Property (P)**: The required observable behavior for an input satisfying `C`, as specified in the numbered correctness properties.
- **Preservation**: Equality of relevant observations between the original function `F` and fixed function `F'` for inputs outside `C`.
- **Live_Watch_Store**: The existing `WatchRepository` implementation (`InMemoryWatchRepository` or `RedisWatchRepository`) that owns active records, runtime sidecars, scheduling, claims, fencing, recovery, and retention.
- **WatchRuntime**: The private live sidecar in `backend/models/watch_runtime.py`; it already carries the authoritative monotonic `revision` and never appears in public `Watch` JSON.
- **Owner_Metadata**: The optional validated `owner_client_id` retained privately with live state. It is a dashboard grouping key, not authentication or authorization.
- **ProjectionEnvelope**: An immutable internal value containing a public `Watch` snapshot, retained owner metadata, and its authoritative source revision.
- **ProjectionPublisher**: An application-level, non-awaiting port that offers a `ProjectionEnvelope` to bounded background processing.
- **Bounded_Handoff**: A finite, non-blocking, per-process buffer that coalesces pending envelopes by watch identifier and reports overflow instead of waiting or growing without limit.
- **History_Store**: The PostgreSQL adapter that conditionally upserts projection envelopes and reads owner-scoped history pages.
- **Source_Revision**: The monotonic revision committed by the authoritative live repository. It, not completion order or wall-clock arrival order, determines whether a projection may replace another.
- **OwnedWatchQuery**: The application use case that merges owner-scoped live and history records, selects live records on identifier collisions, and returns a public page.
- **LIVE**: An owner-list item whose authoritative record is still retained by the live repository.
- **HISTORY_ONLY**: An owner-list item present only in durable history, including terminal records after live retention cleanup.
- **Keyset_Cursor**: An opaque, versioned continuation token for the immutable descending sort key `(created_at, watch_id)`.
- **History_Readiness**: The existing additive `ready`/`degraded`/`unknown` signal. Successful or failed history operations provide evidence; the value never changes the top-level `/health` status meaning.
- **Standalone_Mode**: PostgreSQL and/or CORS intentionally omitted from configuration. Core `/api/*` and `/health` behavior remains available.
- **Configured_Capability**: An optional capability whose environment variable is present. Once present, invalid syntax, connection failure, or initialization failure is a startup error rather than an implicit disable signal.

## Bug Details

### Bug Condition

Let `U` be all boundary scenarios reachable through completed Milestone 4 tasks 1–7. The buggy subset is:

`C = { X ∈ U | distributed(X) ∨ slowFailedOrReorderedHistory(X) ∨ ownerRecovery(X) ∨ liveHistoryConflict(X) ∨ unavailableOrPagedOwnerRead(X) ∨ malformedIdentity(X) ∨ explicitConfigFailure(X) ∨ migrationOrPackagingFailure(X) ∨ secretExposure(X) ∨ contractDrift(X) }`.

**Formal Specification:**

```text
FUNCTION isBugCondition(input)
  INPUT: input of type Milestone4BoundaryScenario
  OUTPUT: boolean

  RETURN input.isWithinCompletedTasks1Through7
         AND (
           input.pollExecutesInDistributedWorker
           OR input.historyWriteIsSlowFailedSaturatedOrOutOfOrder
           OR input.initialOwnerProjectionFails
           OR input.liveStateDiffersFromHistory
           OR input.ownerHistoryIsUnavailableOrExceedsOnePage
           OR input.clientIdentifierIsPresentAndMalformed
           OR input.configuredDependencyIsInvalidOrUnreachable
           OR input.migrationIsMissingFailsOrLeaksResources
           OR input.postgresFailureContainsSensitiveDsnData
           OR input.publicHttpContractDrifts
         )
END FUNCTION
```

The corresponding behavior predicate maps each condition to its validated requirement while keeping the live state machine authoritative:

```text
FUNCTION expectedBehavior(input, result)
  INPUT: input of type Milestone4BoundaryScenario
  INPUT: result of type Milestone4BoundaryObservation
  OUTPUT: boolean

  IF input.pollExecutesInDistributedWorker THEN
    REQUIRE result.projectionUsesSharedPublisherContract
    REQUIRE result.workerResultAndRetrySemanticsAreUnchanged
  END IF

  IF input.historyWriteIsSlowFailedSaturatedOrOutOfOrder THEN
    REQUIRE result.liveCommitAndDispatchDoNotWaitForPostgres
    REQUIRE result.handoffSizeIsBounded
    REQUIRE result.persistedRevisionNeverRegresses
    REQUIRE result.failureIsLoggedOrReflectedInReadiness
  END IF

  IF input.initialOwnerProjectionFails THEN
    REQUIRE result.ownerRemainsInPrivateLiveMetadata
    REQUIRE result.laterProjectionRestoresOwner
  END IF

  IF input.liveStateDiffersFromHistory THEN
    REQUIRE result.recordsAreReconciledByWatchId
    REQUIRE result.liveRecordWins
    REQUIRE result.historyOnlyTerminalRecordsRemainVisible
  END IF

  IF input.ownerHistoryIsUnavailableOrExceedsOnePage THEN
    REQUIRE result.unavailableIsDistinctFromEmpty
    REQUIRE result.everyStableOwnedRecordIsReachableExactlyOnce
    REQUIRE result.orderIsCreatedAtThenWatchIdDescending
  END IF

  IF input.clientIdentifierIsPresentAndMalformed THEN
    REQUIRE result.isStableClientSafeValidationFailure
    REQUIRE NOT result.watchWasCreatedAsUnowned
  END IF

  IF input.configuredDependencyIsInvalidOrUnreachable
     OR input.migrationIsMissingFailsOrLeaksResources THEN
    REQUIRE result.startupFailsWithSanitizedConfigurationError
    REQUIRE result.createdResourcesAreClosedExactlyOnce
  END IF

  IF input.postgresFailureContainsSensitiveDsnData THEN
    REQUIRE result.observableSurfacesContainNoCredentialsOrSensitiveQuery
  END IF

  IF input.publicHttpContractDrifts THEN
    REQUIRE result.contractCheckFailsDeterministically
  END IF

  RETURN all applicable REQUIRE statements hold
END FUNCTION
```

### Current Repository Control and Data Flows

1. **API-local creation**: `backend/main.py` or `backend/api/routes/watches.py` parses `X-Dibs-Client-Id`, calls `WatchService.create`, commits the live watch and runtime, awaits `_history.record(...)`, and only then dispatches the first poll. The exception is swallowed, but latency is not.
2. **API-local transition**: `WatchService._commit_window`, `cancel`, and legacy methods await the same recorder after a live transition. Later calls carry no owner.
3. **Distributed transition**: `backend/workers/tasks/monitor_watch.py::build_watch_service` composes Redis, mock booking, and Celery but supplies no history recorder or PostgreSQL lifecycle.
4. **History write**: `WatchHistoryRepository.record` unconditionally overwrites status, update time, expiry, and JSON on conflict. `COALESCE` can preserve an owner only if a prior owner write succeeded.
5. **Owner read**: `/api/watches/mine` reads `request.app.state.watch_history` directly, returns a bare `list[Watch]`, uses history only, and returns `[]` both when no records exist and when history is disabled.
6. **History ordering**: `list_for_owner` orders by `updated_at DESC` and defaults to `LIMIT 100`, with no stable tie-breaker or continuation metadata.
7. **Identity**: `extract_client_id` maps both omission and malformed input to `None`; creation then succeeds as unowned.
8. **Startup**: `_attach_postgres` and `_configure_cors` catch configuration errors and silently disable explicitly requested capabilities. Migration errors outside `ConfigurationError` can escape after pool creation without local cleanup.
9. **Secrets and packaging**: `create_pool` embeds the complete DSN in `ConfigurationError`; migration discovery uses a filesystem path while `pyproject.toml` does not declare SQL package data.
10. **Health**: only projection writes update `history_readiness`; disabled/read-failed history can still look empty or retain stale readiness.

### Affected Components and Interfaces

| Current component | Current responsibility | Structural defect | Correct boundary |
|---|---|---|---|
| `backend/services/watch_service.py` | Live lifecycle plus awaited history recorder | Service imports DB-layer protocol and awaits optional I/O | Depend on application `ProjectionPublisher.offer(envelope)` only |
| `backend/db/repositories/watch_history.py` | SQL write/read plus readiness decorator | Persistence concerns leak upward; last completion wins | Implement application reader/writer ports; conditional revision upsert |
| `backend/models/watch_runtime.py` | Private authoritative runtime | Does not retain owner needed by later processes | Add optional private owner metadata while preserving all state-machine fields |
| `backend/db/repositories/watches.py` and Lua scripts | Atomic live state and transition results | Projection metadata is not returned with committed state | Return additive private owner/revision metadata without changing decisions |
| `backend/api/client_identity.py` | Header parsing | Omission and malformed values collapse | Return omitted/valid distinctly; raise typed invalid-input error |
| `backend/api/routes/watches.py` | HTTP routes | `/mine` reaches concrete `app.state` repository | Depend on `OwnedWatchQuery`; return public page/error DTOs |
| `backend/api/dependencies.py` | API composition access | No history application dependency | Expose substitutable query use case, not repository |
| `backend/main.py` | API composition/lifespan/CORS | Best-effort configured startup and process-local-only history wiring | Compose common history runtime; fail configured initialization; keep omission standalone |
| `backend/workers/tasks/monitor_watch.py` | Worker process composition and poll task | Omits history and PostgreSQL lifecycle | Use the same projection port/runtime factory with worker-owned resources |
| `backend/config.py` | Postgres/CORS settings | Partial URL validation and secret-bearing errors | Strict parsers, capability-state distinction, safe target descriptions |
| `backend/db/postgres.py` | Pool and migrations | Narrow error normalization, no required-resource/schema assertion | Safe startup abstraction with package discovery, verification, and cleanup |
| `backend/services/readiness.py` | Evidence-based readiness | Write-only history evidence | Record startup-independent read/write/overflow outcomes |
| `tests/*` | Sequential happy paths and immediate failures | Encodes several defects as expected behavior | Deterministic concurrency, worker, migration, pagination, and contract checks |

### Examples

- A `FOUND` poll in Celery returns the normal worker result but never reaches `watch_history`; the dashboard keeps an older `ACTIVE` row. After the fix, the worker offers revision `r+1` through the same projection port used locally, without changing its result dictionary.
- A fake history writer waits forever. Today `POST /api/watches` waits before first-poll dispatch. After the fix, the live watch and zero-delay dispatch complete while the bounded consumer remains blocked.
- Creation for `visitor-1` commits live state, then its first SQL write fails. A later poll currently sends `owner=None` and creates an unowned row. After the fix, the later envelope carries `visitor-1` from private live metadata.
- Revision 8 (`CANCELLED`) reaches PostgreSQL before revision 7 (`ACTIVE`). Today revision 7 can overwrite it. After the fix, the conditional upsert rejects revision 7.
- History says revision 4 is `ACTIVE`, while Redis retains revision 5 as `FOUND`. Today `/mine` returns `ACTIVE`; after the fix, the merged item contains the live `FOUND` watch and `tracking_state="LIVE"`.
- `X-Dibs-Client-Id: has a space` currently creates an unowned watch with 201. After the fix, it returns the stable 422 invalid-client error and creates nothing. Omitting the header still creates an unowned watch with the existing response.
- A valid owner with no configured PostgreSQL currently receives `200 []`, indistinguishable from no watches. After the fix, the owner receives a sanitized 503 history-unavailable error; an available empty store returns a 200 page with no items.
- 235 watches whose update order differs from creation order currently return at most the first 100 updates. After the fix, repeated keyset requests return all 235 in `(created_at DESC, watch_id DESC)` order without duplicates.
- `FRONTEND_ORIGINS=https://user:secret@app.example.com/path?x=1` currently passes some component combinations. After the fix, any user information, non-root path, query, fragment, or invalid port aborts application construction with no secret echo.
- A pool opens and migration SQL raises a driver exception. Today the exception may bypass normalization and the local pool reference is not closed. After the fix, startup emits one sanitized configuration failure and closes the pool exactly once.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**

- Header omission remains valid for both watch-creation paths: the watch is unowned, the existing success status/body is preserved, and the record remains visible through the existing unscoped list (Requirements 3.1 and 3.4).
- `POST /api/parse-and-book` retains every existing execution status and field meaning; `WATCH_CREATED` retains `watch_id` (Requirement 3.2).
- Public `Watch` JSON retains exactly the existing fields and `WatchStatus`/`WatchPollOutcome` values. Owner, source revision, queue state, and projection metadata remain private (Requirement 3.3).
- `GET /api/watches`, `GET /api/watches/{watch_id}`, and `DELETE /api/watches/{watch_id}` retain their status/body contracts. Anonymous ownership is not promoted into authorization (Requirement 3.4).
- The existing live repository, claim/fencing protocol, dispatch/recovery behavior, and retry decisions remain the sole correctness authority. PostgreSQL cannot roll back or veto live state (Requirements 3.5 and 3.6).
- With PostgreSQL and CORS omitted, the backend starts and serves the same standalone routes. CORS remains absent when unconfigured and remains exact-origin, no-wildcard, and no-credentials when configured (Requirements 3.7 and 3.8).
- Owner lists remain isolated by opaque identifier and retain history-only terminal records after live cleanup (Requirement 3.9).
- `/health` retains every existing field and top-level `status` meaning. `history_readiness` keeps the existing vocabulary (Requirement 3.10).
- The Celery task retains its result shape, recoverable exception tuple, retry count/countdown, non-recoverable propagation, serialized runner use, and idempotent cleanup (Requirement 3.11).
- The future `apps/web` remains separately deployable and communicates only through HTTP. The FastAPI backend never imports frontend code and remains independently deployable (Requirement 3.12).
- Existing Milestone 1–3 and completed Milestone 4 tests remain enabled and unweakened; automated validation remains free of live PostgreSQL, Redis, browser, broker, and provider dependencies (Requirement 3.13).

**Scope:**

For every `X ∉ C`, the fix preserves the relevant observation:

```text
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT observable(Milestone4Boundary(input))
         = observable(Milestone4BoundaryFixed(input))
END FOR
```

Observations include HTTP status/body/header shapes, watch and runtime decisions, schedules, worker retry/result behavior, readiness fields, owner isolation, and cleanup. New private metadata, background projection scheduling, logs, and the explicitly revised `/api/watches/mine` collection/error contract are not changes to a preserved public `Watch` value.

## Hypothesized Root Cause

Repository analysis makes the following causes concrete; exploratory tests must still reproduce each defect before implementation so a refuted hypothesis can be revised.

1. **Persistence protocol in the service layer**: `WatchService` imports `WatchHistoryRecorder` from `backend.db.repositories.watch_history`, reversing the desired application-to-adapter dependency and encouraging direct awaited database calls.
2. **Async exception handling mistaken for asynchronous execution**: `_record_history` catches exceptions but awaits the write. Catching failure protects the result value, not latency, dispatch order, or saturation.
3. **Ownership attached to an attempt, not state**: `owner_client_id` is passed only to the creation projection call and is absent from `Watch`, `WatchRuntime`, later poll/cancel transitions, and worker state. `COALESCE` cannot recover data that was never stored.
4. **Arrival-order projection**: `_UPSERT_SQL` has no source revision predicate. Concurrent API/worker writers therefore make completion timing the source of truth instead of the live repository.
5. **Projection-only dashboard query**: `/mine` bypasses `WatchService` and any query use case, so it cannot reconcile live data and directly couples HTTP behavior to repository availability and defaults.
6. **Identity state collapse**: `extract_client_id` returns `None` for both absent and invalid values, eliminating the information required for a recoverable validation response.
7. **Availability state collapse**: a `None` repository and an empty owner result both become `[]`; storage exceptions are not translated at a stable application boundary.
8. **Repository default exposed as HTTP contract**: update ordering and `limit=100` leak directly through a bare list response, with no cursor or stable tie-breaker.
9. **Split composition roots**: API startup injects history, while the Celery worker independently rebuilds `WatchService` without it. Tests mostly substitute the task service and do not assert production-shaped composition.
10. **Omitted and broken configuration treated alike**: `_attach_postgres` and `_configure_cors` intentionally swallow errors despite requirements that a present, invalid setting fail startup.
11. **Incomplete origin grammar**: CORS parsing checks scheme/netloc and a path subset but not user information, query, fragment, canonical root slash, or valid port access/range.
12. **Incomplete migration lifecycle**: only selected connection failures become `ConfigurationError`; the pool is published/closed only on the success path, an empty migration set is accepted, and schema presence is not verified.
13. **Secret-bearing diagnostics**: raw `settings.dsn` and potentially raw driver messages are embedded in startup exceptions and can flow to logs.
14. **Tests model the initial implementation rather than the distributed contract**: current tests assert synchronous recorder calls, malformed-as-anonymous behavior, empty-on-disabled history, silent configured degradation, update ordering, and bare lists. They omit blocked I/O, cross-process ordering, package builds, and public consumer drift.

## Correctness Properties

Property 1: Bug Condition - Distributed Non-Blocking History Projection

_For any_ committed watch creation or transition executed in the API-local or distributed-worker path, the fixed system SHALL offer the same owner-aware, revisioned projection through a bounded non-blocking application contract, and slow, failed, or saturated projection work SHALL NOT delay or alter the live commit, dispatch, worker result, or retry classification.

**Validates: Requirements 2.1, 2.2**

Property 2: Preservation - Live Coordination and Worker Semantics

_For any_ watch lifecycle or worker input where projection state is irrelevant to the Milestone 3 decision, the fixed system SHALL produce the same claim, fence, commit, scheduling, retry, result, and cleanup observations as the original system, including successful live results after projection failure.

**Validates: Requirements 3.5, 3.6, 3.11**

Property 3: Bug Condition - Retained Ownership and Explicit Identity Validation

_For any_ valid client identifier accepted during watch creation, the fixed system SHALL retain that identifier in private authoritative metadata and include it in every later projection envelope even if the first projection fails; for any present malformed identifier, it SHALL return the documented validation error and create no unowned watch.

**Validates: Requirements 2.3, 2.6**

Property 4: Bug Condition - Monotonic Projection Freshness

_For any_ set of projection envelopes for one watch delivered in any order and with duplicates, the persisted public snapshot SHALL equal the envelope with the greatest authoritative source revision, and an older revision SHALL never replace a newer one.

**Validates: Requirements 2.4**

Property 5: Bug Condition - Complete Live-Wins Owner Listing

_For any_ stable owner-scoped live/history datasets and any allowed page size, traversing the fixed collection SHALL return each owned watch identifier exactly once in `(created_at DESC, watch_id DESC)` order, SHALL choose the live snapshot for every overlap, and SHALL retain history-only records after live cleanup.

**Validates: Requirements 2.5, 2.8**

Property 6: Bug Condition - Unavailable History Is Not Empty History

_For any_ valid owner listing request, the fixed system SHALL return an empty success page only after a successful available read with zero reconciled records; disabled or failed history SHALL return the stable sanitized unavailable error and SHALL record degraded readiness after the observed unavailable path.

**Validates: Requirements 2.7**

Property 7: Bug Condition - Substitutable Application and HTTP Boundary

_For any_ frontend-facing tasks 1–7 request, the fixed route SHALL invoke an application-level contract that can be replaced by a fake without concrete database or process-state knowledge, and its request, success, collection, pagination, and error shapes SHALL match the documented public OpenAPI contract.

**Validates: Requirements 2.9**

Property 8: Bug Condition - Explicit Configuration and Strict Origins

_For any_ PostgreSQL or frontend-origin setting, omission SHALL select supported standalone behavior, a valid configured value SHALL initialize the capability, and any explicitly invalid/unreachable value—including an origin with components outside HTTP(S), host, and valid optional port—SHALL fail startup with a sanitized configuration error.

**Validates: Requirements 2.10, 2.11**

Property 9: Bug Condition - Migration Integrity and Secret-Safe Failure

_For any_ migration/package/pool failure after PostgreSQL is configured, the fixed startup SHALL publish no history service, SHALL close each created resource exactly once, SHALL fail if required ordered migrations or schema are absent, and SHALL expose no credential or sensitive query value in exceptions, logs, health, or HTTP.

**Validates: Requirements 2.12, 2.13**

Property 10: Bug Condition - Deterministic Boundary Validation

_For any_ scenario in the validated bug-condition domain, the tasks 1–7 test suite SHALL reproduce the pre-fix counterexample and verify the corrected behavior with deterministic fakes or contract artifacts without contacting a live external service.

**Validates: Requirements 2.14**

Property 11: Preservation - Existing Creation and Public Model Contracts

_For any_ existing caller that omits client identity or uses parse-and-book, unscoped listing, or watch-by-id routes, the fixed system SHALL preserve existing success statuses, body fields, execution meanings, public `Watch` fields/enums, and non-authorizing by-id behavior.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4**

Property 12: Preservation - Standalone and CORS Behavior

_For any_ deployment with PostgreSQL and frontend origins omitted, or any non-browser request, the fixed backend SHALL preserve standalone startup and request behavior; when CORS is validly configured, it SHALL preserve explicit origins/methods/headers with no wildcard origin or credentials.

**Validates: Requirements 3.7, 3.8**

Property 13: Preservation - Owner Isolation and Durable Terminal Visibility

_For any_ two distinct valid client identifiers, the fixed owner query SHALL never return one identifier's records to the other, and SHALL continue returning that owner's durable history-only terminal records after live-store cleanup.

**Validates: Requirements 3.9**

Property 14: Preservation - Health Contract

_For any_ queue, recovery, and history readiness observations, the fixed `/health` response SHALL retain every pre-existing field and top-level `status` meaning, with history expressed only as `ready`, `degraded`, or `unknown`.

**Validates: Requirements 3.10**

Property 15: Preservation - Deployment Independence and Regression Suite

_For any_ supported build or automated validation run, the fixed FastAPI backend SHALL remain independently deployable with no frontend source import, and every existing Milestone 1–3 and completed Milestone 4 assertion SHALL continue to pass without a live service dependency or weakened expectation.

**Validates: Requirements 3.12, 3.13**

## Fix Implementation

### Architectural Direction

```text
Future apps/web (HTTP only)
          |
          v
FastAPI routes + public request/response DTOs
          |
          +--> WatchService ----------------------> Live_Watch_Store
          |         |                                  (authority)
          |         +--> ProjectionPublisher.offer()
          |                    |
          |                    v
          |              Bounded_Handoff
          |                    |
          |                    v
          |              History_Store writer
          |
          +--> OwnedWatchQuery
                    |                         |
                    v                         v
             OwnedLiveWatchReader      History_Store reader
                    \                         /
                     +---- merge by watch_id -+
                              live wins
```

Application ports live above persistence (for example, `backend/services/watch_history.py` or a focused `backend/application/` package). `backend/db/*` implements them. Neither `WatchService` nor HTTP routes import `WatchHistoryRepository`.

### Application-Level Contracts

```text
TYPE ProjectionEnvelope
  watch: Watch
  owner_client_id: string OR null
  source_revision: non-negative integer
END TYPE

ENUM ProjectionOffer
  ACCEPTED
  COALESCED
  REJECTED_FULL
  REJECTED_CLOSED
END ENUM

INTERFACE ProjectionPublisher
  FUNCTION offer(envelope: ProjectionEnvelope) -> ProjectionOffer
  // Synchronous, constant-time, non-awaiting, and exception-contained.
END INTERFACE

INTERFACE HistoryWriter
  ASYNC FUNCTION record(envelope: ProjectionEnvelope) -> WriteDisposition
  // APPLIED, STALE_IGNORED, or DUPLICATE.
END INTERFACE

INTERFACE HistoryReader
  ASYNC FUNCTION list_page(owner_client_id, before_key, limit)
    -> SortedHistoryPage
END INTERFACE

INTERFACE OwnedLiveWatchReader
  ASYNC FUNCTION list_page(owner_client_id, before_key, limit)
    -> SortedLivePage
END INTERFACE

INTERFACE OwnedWatchQuery
  ASYNC FUNCTION execute(owner_client_id, cursor, limit) -> OwnedWatchPage
  RAISES HistoryUnavailable, InvalidCursor
END INTERFACE
```

`NoopProjectionPublisher` is used in standalone mode. A valid owner query in standalone mode uses an explicit unavailable `HistoryReader`, not a `None` repository and not an empty list.

### Corrected Write and Dispatch Flows

**Creation:**

1. The HTTP dependency distinguishes an omitted header from a present value. It validates a present value before orchestration or watch creation.
2. `WatchService.create` builds the unchanged public `Watch` and a private runtime containing `owner_client_id` and revision 0.
3. The live repository atomically creates the watch, private runtime, and first schedule marker exactly as today; owner is additive metadata ignored by all decision logic.
4. The existing first-poll dispatch runs with the same zero-delay and durable-marker recovery semantics.
5. The service constructs a revision-0 `ProjectionEnvelope` from the committed internal result and calls `offer`. It never awaits PostgreSQL. Rejection is logged and marks history degraded but does not change the 201/200 result or dispatch.

**Poll, cancellation, and expiry:**

1. The live repository performs the existing claim/fence/commit or conditional transition unchanged.
2. Additive internal transition metadata returns the committed revision and retained owner with the public watch. No public response model changes.
3. The existing notification and successor-dispatch behavior remains in its current order.
4. Exactly one envelope for a committed revision is offered. Fenced, unknown, or non-committed operations do not manufacture a newer envelope. Duplicate offers are safe.
5. Legacy records without a sidecar use the existing migration path to obtain private runtime metadata before a projected transition; pre-Milestone-4 records default to no owner.

**Distributed worker:**

1. API and worker composition call one history-runtime factory and inject the same `ProjectionPublisher` interface into `WatchService`.
2. The Celery task still executes `poll_once`/`poll_window` under the serialized `asyncio.Runner` and returns the same dictionary.
3. A worker projection consumer has an independently running lifecycle so work is not paused when `Runner.run(...)` returns. It owns its event loop/thread and PostgreSQL write resources; the task only performs a non-blocking offer.
4. Projection startup/configuration failures in a configured worker fail worker-process initialization. Runtime offer/write failures are logged and contained and never enter the existing recoverable Redis/Kombu retry tuple.
5. Shutdown stops acceptance, drains only to a bounded deadline, closes history resources, closes Redis through the existing runner, and closes the runner. Repeated Celery/`atexit` signals remain idempotent.

### Bounded Asynchronous Projection

The handoff has a configured finite capacity with bounded validation. It maintains a deque of watch identifiers and a map of the newest pending envelope per identifier:

- Offering a new watch below capacity appends one identifier.
- Offering the same watch replaces its pending envelope only when the source revision is newer; capacity does not increase.
- Offering a stale or duplicate revision is a no-op/coalesce result.
- Offering a new watch at capacity returns `REJECTED_FULL` immediately, logs structured non-secret context, and marks history degraded.
- Closing the publisher returns `REJECTED_CLOSED`; it never blocks a live operation.
- The consumer removes one pending identifier at a time, invokes the async writer, records success/failure, and continues after errors. Database statement timeout remains a second bound, not the live-path bound.

One consumer per process gives deterministic local ordering; the SQL revision predicate supplies correctness across API/worker processes and retries. The buffer does not claim guaranteed delivery: overflow or sustained PostgreSQL failure is an observable projection failure, while the live operation remains successful as required.

### Retained Ownership and Monotonic Freshness

`WatchRuntime` gains an optional bounded `owner_client_id` as private schema metadata. It is written with `create_with_schedule`, preserved by `model_copy` and Redis Lua transitions, returned in internal claims/results, and removed only with normal live retention cleanup. It is never added to `Watch`, API bodies, notification text, task arguments, or logs.

Internal `CreateResult`, `CommitResult`, and `TransitionResult` gain an additive projection metadata value containing owner and committed revision. In-memory and Redis implementations derive it from the same authoritative runtime update that already increments revision. This exposes facts from the atomic transition; it does not add a new decision or PostgreSQL dependency.

A new ordered migration adds `source_revision BIGINT NOT NULL` and a listing index on `(owner_client_id, created_at DESC, watch_id DESC)`. The history upsert follows these rules:

1. Insert a missing row with its envelope revision.
2. Replace public state only when `EXCLUDED.source_revision > watch_history.source_revision`.
3. Treat equal revisions as idempotent. An equal-revision retry may fill a currently null owner from the same retained metadata but cannot replace public state.
4. Preserve the first non-null owner. A conflicting non-null owner is an invariant error, is logged without identifier content, and never reassigns the row.
5. Never update immutable `created_at` or `watch_id` on conflict.

This rule protects against task retries, API/worker races, connection timing, and out-of-order consumer completion. `updated_at` remains a public display field, not a concurrency token.

### Live/History Reconciliation and Pagination

`OwnedWatchQuery` requires available history for a valid owner because returning a live-only subset would falsely claim a complete dashboard. It reads bounded sorted chunks from both sources and performs a two-way merge:

1. Both sources use the same key `(created_at DESC, watch_id DESC)` and the cursor's exclusive lower boundary.
2. When both source heads have the same `watch_id`, emit the live `Watch`, label it `LIVE`, and advance both.
3. Otherwise emit the next sort key. Live-only records are `LIVE`; history-only records are `HISTORY_ONLY`.
4. Continue until `limit + 1` unique identifiers or both sources are exhausted. The extra item determines `has_more` and `next_cursor`.
5. Never use `updated_at` for ordering. Creation keys are immutable, so later state updates cannot move an item between pages.
6. A traversal over a stable dataset returns each item exactly once. Watches created after traversal starts appear on a fresh first-page request rather than being inserted into an older continuation.

The live adapters maintain a private owner index beside existing all/active indexes. Creation and cleanup update it atomically with private runtime metadata. Existing `list_all`, `list_active`, get, and delete contracts are unchanged.

### Public HTTP Contracts

The future frontend consumes only these HTTP schemas and never imports backend Python modules.

| Endpoint | Success contract | Corrected/new errors | Preserved behavior |
|---|---|---|---|
| `POST /api/parse-and-book` | `200 PromptExecutionResult` | Present malformed client id: `422 PublicError` | All execution statuses/fields, including `WATCH_CREATED.watch_id` |
| `POST /api/watches` | `201 Watch` plus existing monitoring headers | Present malformed client id: `422 PublicError` | Omitted id, body shape, policy headers, dispatch behavior |
| `GET /api/watches` | `200 list[Watch]` | Existing errors | Global/unscoped and `active_only` behavior |
| `GET /api/watches/mine?limit=&cursor=` | `200 OwnedWatchPage` | `422 PublicError` for malformed id/cursor/limit; `503 PublicError` for unavailable history | Owner isolation and history-only terminal visibility |
| `GET /api/watches/{watch_id}` | `200 Watch` | Existing 404 body | No ownership authorization |
| `DELETE /api/watches/{watch_id}` | `200 Watch` | Existing 404 body | No ownership authorization; existing cancellation result |
| `GET /health` | Existing exact field set | None added | Existing top-level status meaning and readiness vocabulary |

`Watch` remains exactly:

`watch_id`, `status`, `query`, `auto_book`, `created_at`, `updated_at`, `expires_at`, `attempts`, `max_attempts`, `last_checked_at`, `next_check_at`, `found_slots`, `booking`, and `last_error`.

The corrected owner collection is an explicit wrapper; it does not add fields to `Watch`:

```json
{
  "items": [
    {
      "watch": { "watch_id": "watch_...", "status": "ACTIVE" },
      "tracking_state": "LIVE"
    }
  ],
  "page": {
    "limit": 50,
    "has_more": true,
    "next_cursor": "opaque-versioned-token"
  }
}
```

The shown `watch` is abbreviated only for readability; the real value is the complete unchanged `Watch`. `tracking_state` is `LIVE` or `HISTORY_ONLY`. Default `limit` is 50 and the maximum is 100. A cursor is URL-safe, opaque to callers, versioned, and encodes only the last emitted `(created_at, watch_id)` key. Empty success is `items=[]`, `has_more=false`, and `next_cursor=null`.

New boundary errors use one stable sanitized schema:

```json
{
  "error": {
    "code": "HISTORY_UNAVAILABLE",
    "message": "Watch history is temporarily unavailable. Please try again.",
    "retryable": true
  }
}
```

Codes are `INVALID_CLIENT_ID` (422, not retryable), `INVALID_PAGINATION` (422, not retryable), and `HISTORY_UNAVAILABLE` (503, retryable). Messages never echo the client token, cursor, DSN, driver text, or internal exception. Existing endpoints retain their established FastAPI `detail` validation/domain errors where preservation requires it; the future frontend fetch wrapper normalizes both documented legacy `detail` forms and `PublicError` into its own client result without rendering raw JSON.

Identity behavior is explicit:

| Header state | Creation | `/mine` |
|---|---|---|
| Omitted | Existing success; privately unowned | Empty `OwnedWatchPage` without a history lookup |
| Present and valid (`[A-Za-z0-9_-]{1,200}` after existing trim) | Existing success; private owner retained | Owner-scoped paginated query |
| Present and malformed | 422; no parse/create side effect | 422; no history lookup |

### Startup, Migration, CORS, Readiness, and Secrets

**Capability state matrix:**

| Capability | Environment omitted | Environment present and valid | Environment present but invalid/unreachable |
|---|---|---|---|
| PostgreSQL history | Standalone publisher; owner history unavailable | Open pool, discover/verify/apply migrations, verify schema, compose reader/writer/projector | Abort API or worker startup with sanitized `ConfigurationError` |
| Frontend CORS | No CORS middleware or headers | Install exact-origin middleware before ASGI stack construction | Abort app construction/startup with sanitized `ConfigurationError` |

This fail-fast rule applies only to the newly configured capability. Existing OpenAI/watch-setting request behavior stays as currently tested.

**PostgreSQL lifecycle:**

1. Parse `PostgresSettings`; the raw DSN field is excluded from object representation.
2. Derive a safe target label containing only scheme class, host, valid port, and database name. User information, password, query, and fragment are always omitted.
3. Create a local pool reference. Do not publish it to application state yet.
4. Discover migrations through package resources, require the known ordered migration set, apply under the existing lock, and verify the `watch_history` table, required columns (including `source_revision`), and owner-created index.
5. Only after verification succeeds, construct and publish application readers/projectors.
6. On any configuration, filesystem/package, driver, migration, or verification failure after resource creation, close the local pool exactly once and raise one sanitized startup error. Do not log raw driver messages or retain a secret-bearing exception cause.
7. Normal shutdown closes each published projector/pool exactly once.

Migration SQL becomes declared package data and is discovered with `importlib.resources`, so wheel/container installs do not depend on a source-tree `Path`. An empty or missing migration package is an error, not a successful no-op. Concurrent API/worker migration attempts remain safe through the existing transaction-scoped lock.

**CORS grammar:**

Each comma-separated origin must have HTTP or HTTPS scheme, a non-empty host, no username/password, a valid optional port in 1–65535, no query or fragment, and no path other than empty or `/`. An accepted root slash is normalized away to the browser `Origin` form. Wildcards are rejected. Middleware keeps methods `GET`, `POST`, and `DELETE`; request headers `Content-Type` and `X-Dibs-Client-Id`; the existing exposed monitoring headers; and `allow_credentials=False`.

**Readiness:**

- Unconfigured and unused history remains `unknown`.
- A completed write or owner read records `ready`; a write/read failure, unavailable valid-owner request, or handoff overflow records `degraded`.
- A later successful operation may recover `degraded` to `ready`, preserving the existing last-observation model.
- Worker write failures are observable in structured logs. The API readiness tracker reflects its own writes and all dashboard reads; no false cross-process shared-memory claim is made.
- `/health` retains exactly `status`, `service`, `config`, `watch_store`, `watch_queue`, `queue_readiness`, `recovery_readiness`, and `history_readiness`. History does not change top-level `status`.

**Secret handling:**

No exception, log record, readiness field, or HTTP response includes a raw DSN or driver message that may contain one. Safe errors identify only a non-sensitive target such as `db.example.com:5432/dibs` and a controlled category (`connection failed`, `migration failed`, or `schema verification failed`). Tests use sentinel username, password, and query values and search every observable surface.

### Specific Changes Required

| File or component | Required design change |
|---|---|
| `backend/api/client_identity.py` | Introduce omitted/valid/invalid distinction and typed invalid-client error; preserve valid trimming and grammar. |
| `backend/api/contracts.py` (new) | Define `PublicError`, `OwnedWatchItem`, `OwnedWatchPage`, page info, tracking-state enum, and OpenAPI examples. |
| `backend/api/dependencies.py` | Add `get_owned_watch_query`; expose application use cases rather than concrete history repository. |
| `backend/api/routes/watches.py` | Validate identity before side effects; route `/mine` through the use case; add bounded limit/cursor and documented responses. |
| `backend/services/watch_history.py` or `backend/application/history.py` (new) | Own projection envelope/publisher/reader ports, bounded projector, cursor codec, reconciliation use case, and typed unavailable errors. |
| `backend/services/watch_service.py` | Replace awaited DB recorder with non-blocking publisher offers built only from committed internal transition metadata. Keep claim/notification/dispatch decisions unchanged. |
| `backend/models/watch_runtime.py` | Add optional private owner metadata with backward-compatible default/schema migration. |
| `backend/db/repositories/watch_decisions.py` | Add private projection metadata to successful create/commit/transition results without changing status enums or public models. |
| `backend/db/repositories/watches.py` and `watch_scripts.py` | Preserve owner/revision atomically, return projection metadata, and maintain a private owner index; retain existing all/active/state-machine behavior. |
| `backend/db/repositories/watch_history.py` | Implement revision-guarded envelope writes and created-at/watch-id keyset reads; remove service-layer protocol ownership. |
| `backend/db/migrations/0002_*.sql` | Add source revision and deterministic owner-list index without rewriting already applied `0001`. |
| `backend/db/migrations/__init__.py`, `pyproject.toml` | Make SQL resources package-visible and included in built artifacts. |
| `backend/db/postgres.py` | Use package discovery, required-set/schema verification, broad controlled normalization, safe target descriptions, and explicit resource ownership. |
| `backend/config.py` | Add bounded projection capacity if configurable; fully validate CORS origins; prevent DSN representation/error leakage. |
| `backend/services/readiness.py` | Record history read/write/overflow evidence while preserving vocabulary and unrelated state. |
| `backend/main.py` | Fail explicitly configured Postgres/CORS initialization, compose common application ports, start/stop bounded projector, and keep omission standalone. |
| `backend/workers/tasks/monitor_watch.py` | Compose the common history runtime in each worker process and add bounded, idempotent history cleanup without changing task results/retries. |
| `infra/docker-compose.yml` (when deployment task proceeds) | Supply the same optional PostgreSQL configuration to API and worker; preserve existing services as required. |
| `tests/*` | Replace defect-expecting assertions and add deterministic exploration, property, composition, HTTP, packaging, and preservation coverage. |

### Requirement Traceability

| Current defect | Correct behavior | Design mechanism | Property / primary check |
|---|---|---|---|
| 1.1 | 2.1 | Common API/worker projection composition | Property 1; worker composition contract |
| 1.2 | 2.2 | Non-awaiting bounded/coalescing handoff | Property 1; blocked-writer latency/capacity check |
| 1.3 | 2.3 | Private owner in authoritative runtime/results | Property 3; failed-first-write recovery |
| 1.4 | 2.4 | Source revision and conditional upsert | Property 4; permutation state machine |
| 1.5 | 2.5 | Identifier merge with live precedence | Property 5; generated overlap sets |
| 1.6 | 2.6 | Three-state header parser and 422 error | Property 3; generated token partition |
| 1.7 | 2.7 | Explicit unavailable reader/error and read readiness | Property 6; disabled/failing/empty matrix |
| 1.8 | 2.8 | Created-at/watch-id keyset pages | Property 5; multi-page traversal |
| 1.9 | 2.9 | Application query/publisher ports and DTOs | Property 7; dependency override/OpenAPI check |
| 1.10 | 2.10 | Omitted-vs-configured capability matrix | Property 8; startup matrix |
| 1.11 | 2.11 | Complete browser-origin grammar | Property 8; generated URL components |
| 1.12 | 2.12 | Package/schema verification and owned cleanup | Property 9; failure-phase/packaged-wheel checks |
| 1.13 | 2.13 | Safe target labels and controlled exceptions | Property 9; sentinel-secret search |
| 1.14 | 2.14 | Deterministic fake and contract suite | Property 10; full boundary matrix |

| Preservation clause | Preserved artifact | Property / regression evidence |
|---|---|---|
| 3.1 | Headerless creation status/body and unowned visibility | Property 11; existing watch API tests |
| 3.2 | `PromptExecutionResult` fields/status meanings | Property 11; all-status contract matrix |
| 3.3 | Exact `Watch`, status, and poll-outcome schemas | Property 11; serialization/OpenAPI snapshot |
| 3.4 | Unscoped and by-id route contracts/no auth boundary | Property 11; existing route tests |
| 3.5 | Live repository and single-flight authority | Property 2; repository oracle/state-machine suites |
| 3.6 | Live success after projection failure | Properties 1–2; failure injection |
| 3.7 | Unconfigured standalone startup/routes | Property 12; cleared-environment TestClient |
| 3.8 | Unconfigured/non-browser behavior and exact CORS | Property 12; CORS matrix |
| 3.9 | Owner isolation and history-only terminal records | Property 13; multi-owner cleanup test |
| 3.10 | Exact health fields/top-level meaning/vocabulary | Property 14; readiness transition matrix |
| 3.11 | Worker result/retry/serialization/cleanup | Property 2; existing worker matrix plus history variants |
| 3.12 | Independent HTTP-only frontend/backend deployment | Property 15; static import/build contract |
| 3.13 | Unmodified deterministic regression suite | Property 15; full validation commands |

## Testing Strategy

### Validation Approach

Validation follows two phases. First, add focused exploration checks against the unfixed code to capture concrete counterexamples and confirm each repository-backed root-cause hypothesis. Then implement the boundary changes and use the same checks for fix verification, followed by preservation checks against existing behavior. No test contacts live PostgreSQL, Redis, Celery broker, browser, OpenAI, or booking provider.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples before implementation. If a check fails for a reason different from the hypothesized cause, revise the cause and design before changing production code.

**Test Plan**: Use fake pools/connections, fake projection consumers, `asyncio.Event` barriers, fake clocks, in-memory/`fakeredis` repositories, direct Celery task invocation, and FastAPI `TestClient`/OpenAPI generation.

**Test Cases**:

1. **Distributed Composition**: Build the current worker service and show that a committed poll emits no history record while the API-local service does.
2. **Blocked Projection**: Hold the current recorder on an event and show create/first dispatch, poll, and cancel remain blocked.
3. **Owner Recovery**: Fail the first create write, allow a later poll write, and show the row is permanently unowned.
4. **Out-of-Order Write**: Complete terminal revision before older active revision and show the active row wins last.
5. **Live/History Conflict**: Seed stale history plus newer live state and show `/mine` returns stale history.
6. **Malformed Identity**: Send present malformed headers to both creation paths and show successful unowned creation.
7. **Unavailable Read**: Compare disabled history and available empty history and show both return `200 []`; force a read error and observe uncontrolled failure/stale readiness.
8. **Ordering and Truncation**: Seed watches with divergent creation/update order and more than 100 rows; show update order and unreachable tail.
9. **Concrete Route Coupling**: Remove/replace `app.state.watch_history` and show the route knows persistence shape rather than an application dependency.
10. **Configured Startup Degradation**: Configure malformed/unreachable PostgreSQL and malformed CORS and show request serving still starts.
11. **Strict-Origin Gaps**: Feed user info, query, fragment, root-path variants, and invalid ports and record incorrectly accepted cases.
12. **Migration Failure and Packaging**: Raise a driver exception after pool creation and discover an empty installed migration resource; show unnormalized failure/leak or accepted absence.
13. **DSN Leak**: Force a connection error using sentinel credentials/query and show them in the current exception/log.
14. **Contract Drift Gap**: Mutate a generated public schema fixture and show no existing frontend-consumer check detects it.

**Expected Counterexamples**:

- Worker projection calls are absent.
- Live requests wait on the blocked recorder.
- Owner, freshness, reconciliation, and pagination observations match defects 1.3–1.8.
- Explicit configuration errors degrade silently, incomplete origins pass, resources leak on selected failures, or secret sentinels appear.
- Existing sequential tests continue to pass despite these boundary failures.

### Fix Checking

**Goal**: Verify all inputs satisfying the bug condition meet their corresponding correctness property.

```text
FOR ALL input WHERE isBugCondition(input) DO
  result := Milestone4BoundaryFixed(input)
  ASSERT expectedBehavior(input, result)
END FOR
```

Every exploration check is retained and inverted to its corrected assertion. Tests target Properties 1 and 3–10; failures report the minimal generated counterexample or the exact fake event sequence.

### Preservation Checking

**Goal**: Verify non-buggy inputs retain original observable behavior.

```text
FOR ALL input WHERE NOT isBugCondition(input) DO
  original := observable(Milestone4Boundary(input))
  fixed := observable(Milestone4BoundaryFixed(input))
  ASSERT fixed = original
END FOR
```

Existing tests are not weakened. Differential fakes compare history-disabled and projection-failing live operations to the baseline for public result, repository state, dispatches, notification events, retry calls, and health fields. Tests target Properties 2 and 11–15.

### Deterministic Test Infrastructure

- Fake `PoolLike`/connection implementations model conditional upserts, keyset reads, transaction failure, and close counts.
- Fake clocks control `created_at`, revisions, retention, cursor order, and readiness observations.
- `asyncio.Event`/barriers prove non-blocking behavior without wall-clock sleeps.
- A fixed-capacity fake handoff exposes occupancy, coalescing, overflow, and close state.
- Worker tests invoke `monitor_watch.run` and process-init/cleanup functions directly with cached factories reset between cases.
- Property generators run with a reproducible seed/derandomized mode and report shrinking counterexamples.
- Network/socket access is denied or monkeypatched in these suites so accidental external calls fail immediately.

### Unit Tests

- Three-state client-id parsing, no-side-effect malformed handling, and safe error serialization.
- Cursor encode/decode/version/bounds and invalid-token behavior.
- Bounded handoff acceptance, per-watch coalescing, stale/duplicate handling, overflow, close, and bounded drain.
- Revision-guarded SQL behavior, owner fill/no-reassignment, and exact public `Watch` round trips.
- Two-way merge for overlaps, live-only/history-only records, stable tie-breaking, page boundaries, and empty results.
- Read/write/overflow readiness transitions without queue/recovery bleed-through.
- Strict Postgres/CORS settings validation and safe target formatting.
- Migration discovery/order/required-set/schema checks and exactly-once cleanup for every failure phase.

### Property-Based Tests

- Generate projection revisions and all completion permutations; persisted state must equal the maximum revision (Property 4).
- Generate owner-partitioned live/history sets, overlaps, terminal cleanup, page sizes, and cursor traversals; output must satisfy uniqueness, completeness, order, live precedence, and isolation (Properties 5 and 13).
- Generate valid/invalid client tokens and origin URL components; classification must be total and match the documented grammar (Properties 3 and 8).
- Generate burst sequences against blocked consumers; live results must match baseline and occupancy must never exceed capacity (Properties 1–2).
- Generate DSNs with sentinel user info and query values plus failure phases; no observable string may contain a sentinel (Property 9).
- Generate public Watch/status/result variants and compare serialization with the existing schema (Property 11).

### Integration Tests

- FastAPI `TestClient` covers headerless/valid/malformed creation, all parse-and-book statuses, owner page success/error/pagination, unchanged unscoped/by-id routes, CORS, and exact `/health` keys.
- API composition tests use fake history runtime startup/shutdown to prove configured fail-fast, standalone omission, bounded projector wiring, and cleanup.
- Worker composition tests prove history is injected, poll outcomes project, Redis/Kombu retry classes remain exact, non-recoverable errors still escape, result dictionaries are unchanged, runner access stays serialized, and shutdown remains idempotent.
- Live/history integration uses in-memory/fakeredis authoritative state plus fake PostgreSQL to prove owner recovery, revision monotonicity, live-wins reconciliation, and history-only terminal visibility.
- Migration packaging tests build or inspect the installed wheel/container artifact without network access and assert required SQL resources are discoverable.
- Contract-drift tests generate FastAPI OpenAPI deterministically, assert endpoint/status/schema components, and compare the future `apps/web` generated client/types to that public artifact. The frontend consumes the artifact or HTTP, never backend Python imports.
- Full regression validation runs `python -m pytest`, `python -m mypy backend`, `python -m compileall backend tests`, and `git diff --check`; no development server, watcher, or live service is started.
