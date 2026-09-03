> **SUPERSEDED — retired 2026-09-03.** Kept for its analysis, not as a plan.
> See [SUPERSEDED.md](SUPERSEDED.md).

# Milestone 4 Frontend Backend Structure Fix Bugfix Design

## Overview

This design expands the previously validated Milestone 4 frontend/backend boundary repair into a coherent complete-backend architecture. It is a design-only phase: no production code, tests, task plan, or requirements are changed here. The future frontend remains a separately deployable HTTP client; there is still no `apps/web` implementation to redesign.

Repository inspection shows that the defects are connected rather than independent. Live watch coordination is correctly centered on the Milestone 3 in-memory/Redis repository and its claims, fencing, schedule markers, recovery, and retention protocol, but passive history, anonymous ownership, notification delivery, settings, lifecycle, topology, migration, validation, health, and deployment concerns are composed around that authority inconsistently. The API and worker build different service graphs; optional writes are awaited; recovery creates terminal state without the normal terminal effects; startup publishes partially initialized resources and teardown stops after the first exception; topology probe failure is treated as non-clustered; several scans are nominally batched only after an unbounded read; and public readiness does not drive role-appropriate deployment checks.

The corrected architecture has five cooperating planes:

1. **Authoritative live-state plane**: the existing Milestone 3 `WatchRepository` protocol remains the only authority for watch state, ownership metadata, revisions, claims, fencing, schedule markers, terminal events, and retention.
2. **Application/effects plane**: application-level use cases receive committed facts and offer passive projection or durable terminal delivery work without waiting for PostgreSQL or a notifier.
3. **Passive read-model plane**: PostgreSQL stores an owner-scoped, revision-monotonic current-state projection. Owner listing reconciles bounded history pages with bounded live pages and always prefers retained live state.
4. **Process-composition plane**: API and worker use the same immutable settings snapshot, topology policy, migration verifier, application ports, and lifecycle ledger, while retaining role-specific queues, probes, and shutdown adapters.
5. **Public-contract plane**: FastAPI routes expose documented request, success, page, and sanitized error schemas. A generated OpenAPI artifact is the frontend boundary; frontend code never imports backend source.

The design is governed by these constraints:

- PostgreSQL, projection queues, notification delivery, and readiness never participate in live claims, leases, fences, commits, booking decisions, or Celery retry classification.
- A committed live result is reported truthfully even when projection or notification work later fails.
- Every scan, handoff, retry pass, drain, and cleanup pass has a configured finite bound and reports remaining backlog.
- API and worker classify Redis topology identically and fail closed before using multi-key atomic scripts.
- Existing sibling specs retain ownership of their mechanisms. This design composes with their extension points instead of replacing their logging, retry, runner, or Milestone 3 state-machine contracts.

## Glossary

- **Bug_Condition (C)**: A complete-backend scenario in which one of Requirements 1.1–1.32 occurs, or an exploration-only scenario in Requirement 1.33 lacks deterministic classification.
- **Property (P)**: The required observation produced for an input in `C`, defined only in the numbered Correctness Properties section.
- **Preservation**: Equality of relevant public and coordination observations between the original system `F` and fixed system `F'` for inputs outside `C`.
- **Live_Watch_Store**: `InMemoryWatchRepository` or `RedisWatchRepository`; the sole watch-state and coordination authority.
- **WatchRuntime_v3**: The private runtime sidecar with a supported schema version, authoritative revision, owner metadata, cadence/claim state, and retention metadata. It never appears in public `Watch` JSON.
- **CommittedFacts**: An immutable internal result returned by an atomic create/commit/transition. It carries the committed `Watch`, `WatchRuntime_v3`, source revision, retained owner, and optional terminal event identifier.
- **ProjectionEnvelope**: A passive history write value containing the public watch snapshot, retained owner, and authoritative source revision.
- **ProjectionPublisher**: A synchronous, non-throwing application port that offers a `ProjectionEnvelope` to finite background work.
- **Projection_Reconciler**: A bounded scanner that re-offers current authoritative live snapshots so overflow or transient projection failure can be repaired while live records remain retained.
- **History_Store**: The PostgreSQL adapter that conditionally writes projection envelopes and reads owner-scoped keyset pages.
- **OwnedWatchQuery**: The application use case that merges live and history pages by watch identifier, selects live state on overlap, and returns a frontend-facing page.
- **TerminalEventRecord**: Private durable metadata atomically created for a `FOUND`, `BOOKED`, or `EXPIRED` transition. It holds an idempotency key, delivery state, retry/lease metadata, and a bounded private notification payload.
- **Delivery_State**: `PENDING`, `IN_FLIGHT`, `RETRYABLE`, `DELIVERED`, `UNCERTAIN`, `EXHAUSTED`, or conservative migration-only `LEGACY_SUPPRESSED`; no delivery state is added to public `Watch`.
- **LifecycleLedger**: A stack/phase-based registry that owns initialized resources, rolls them back in safe reverse order, isolates close exceptions, and closes each registered resource exactly once.
- **StartupSnapshot**: An immutable capture of the process environment plus either the parsed value or retained sanitized error for every settings family. Request dependencies never parse the environment again.
- **TopologyDecision**: The common Redis classification `SUPPORTED_PRIMARY`, `UNAVAILABLE`, `UNSUPPORTED`, or `UNKNOWN`. Only `SUPPORTED_PRIMARY` may back atomic repositories.
- **AuthorityGuard**: Recovery leadership state with an owner, expiry, renewal margin, and stop-on-loss rule.
- **Stable_Page**: A bounded page with an opaque continuation cursor over an immutable ordering key and an explicit `has_more`/backlog observation.
- **Dispatch_Horizon**: The maximum future interval sent to a queue; the recovery scheduler wakes before a marker enters that horizon.
- **CalendarPolicy**: The shared deterministic date, horizon, closure, sellout, and slot-boundary policy used by direct API and orchestrated requests.
- **MigrationSet**: The ordered packaged migration entries, each with version, name, SHA-256 checksum, plus a checksum-derived set identity and expected schema fingerprint.
- **Liveness**: Evidence that a role process/event loop is alive; it does not imply dependency or application readiness.
- **Readiness**: Role-specific semantic evidence that every capability required by the selected configuration can serve work now.
- **Exploration_Gate**: A deterministic characterization checkpoint that must reproduce and classify an unproven hypothesis before any public behavior is changed.

## Bug Details

### Bug Condition

Let `U` be the set of complete-backend scenarios reachable through API, application services, repositories, workers, recovery, configuration, lifecycle, provider adapters, migrations, packaging, and deployment. The buggy set is the union:

`C = C_projection ∪ C_contract ∪ C_lifecycle ∪ C_topology ∪ C_validation ∪ C_privacy ∪ C_delivery ∪ C_boundedness ∪ C_migration ∪ C_health ∪ C_exploration`.

**Formal Specification:**

```text
FUNCTION isBugCondition(input)
  INPUT: input of type CompleteBackendScenario
  OUTPUT: boolean

  RETURN input.distributedOutcomeCanDivergeFromLocalOutcome
      OR input.optionalProjectionCanDelayLoseOrRegressState
      OR input.liveAndHistoricalStateDisagree
      OR input.startupSnapshotAndRequestBehaviorDisagree
      OR input.partialLifecycleFailureCanLeakOrSkipCleanup
      OR input.redisTopologyIsUnsupportedUnknownOrInconsistentlyChecked
      OR input.inputOrPersistedModelViolatesARequiredInvariant
      OR input.catalogCalendarOrTemporalPolicyIsInconsistent
      OR input.secretOrReservationDetailCanEscapeToDiagnostics
      OR input.terminalSideEffectIsLostDuplicatedOrAmbiguous
      OR input.repositoryRecoveryOrCleanupWorkIsUnboundedOrLate
      OR input.retentionOrMigrationIntegrityCannotBeProven
      OR input.roleHealthOrDeploymentCompositionIsInvalid
      OR input.crossModuleContractLacksDeterministicEvidence
      OR input.requiresExplorationClassification
END FUNCTION
```

The expected-behavior predicate delegates each classified condition to its paired 2.x clause and otherwise requires preservation:

```text
FUNCTION expectedBehavior(input, result)
  INPUT: input of type CompleteBackendScenario
  INPUT: result of type CompleteBackendObservation
  OUTPUT: boolean

  IF isBugCondition(input) THEN
    FOR EACH requirement IN pairedExpectedRequirements(input) DO
      REQUIRE result satisfies requirement
    END FOR
  ELSE
    REQUIRE observable(result) = observableOriginal(input)
  END IF

  RETURN every applicable REQUIRE statement holds
END FUNCTION
```

### Current Repository Evidence

| Concern | Current evidence | Consequence |
|---|---|---|
| Projection composition | `WatchService._record_history` imports a DB-layer protocol and awaits it; `monitor_watch.build_watch_service` supplies no history adapter | API latency depends on PostgreSQL and worker outcomes are never projected |
| Ownership/freshness | Owner is only an argument to the first write; `WatchRuntime` has no owner; SQL upsert has no revision predicate | Failed first writes lose ownership and late writes can regress terminal state |
| Owner query | `/api/watches/mine` reads `app.state.watch_history` directly, returns a bare repository list, and maps disabled history to `[]` | Persistence leaks into HTTP; unavailable and empty are indistinguishable |
| Recovery terminal effects | `RecoveryCoordinator._reconcile_one` calls `expire_if_eligible` and counts `expired`, but does not project or notify | Recovery expiry can remain `ACTIVE` in history and undelivered to the notifier |
| Notification delivery | The repository retains only a terminal event ID set; `_commit_terminal` awaits the notifier after commit | A throwing notifier turns committed success into an ambiguous exception and redelivery sees only a terminal no-op |
| Settings | `get_orchestrator` falls back to `Settings.from_environment()` when the startup value is absent | Environment mutation can change request behavior after health and composition captured a different failure |
| Lifecycle | `lifespan` acquires resources incrementally and closes recovery, queue, Redis, PostgreSQL, and orchestrator sequentially | Startup failure can leak earlier resources; one shutdown exception skips all later closes |
| Topology | `_redis_cluster_enabled` catches every exception and returns `False`; the worker performs no topology gate | Unknown topology is accepted as standalone and API/worker can disagree |
| Worker cleanup | `_close_worker_resources` returns when the runner cache is empty, even if `_redis_client` was initialized | Redis can leak when initialization order differs from the assumed runner-first path |
| Diagnostics | Postgres errors embed `settings.dsn`; Redis settings/logs expose raw URLs; dataclass representations contain connection strings | Credentials and sensitive query values can escape through exceptions, logs, reprs, health, or HTTP |
| Temporal/model policy | Direct watch creation checks only a past date; `AvailabilityQuery` accepts a reversed window when no preferred time exists; models accept non-UTC values and `model_copy(update=...)` bypasses validation | Direct callers can bypass orchestrator policy and reconstructed/updated state can violate ordinary construction invariants |
| Venue calendar | `_week(..., sunday=None)` means “inherit weekday,” so it cannot also mean closed; `SUPPORTED_YEARS` is fixed while accepted dates roll forward | Closed Sundays may appear open and accepted dates can outrun closure/sellout data |
| Privacy | `LoggingNotificationService` logs watch ID, venue, date, party size, and attempts | Operational logs contain unnecessary reservation details |
| Scan/authority bounds | Redis recovery starts with `SMEMBERS`, then sequentially reads each record; in-memory and pin cleanup build whole due collections; leadership is renewed only before a pass | Nominal batch sizes do not bound reads/memory, and a long pass can continue after authority expires |
| Wake/readiness | Recovery sleeps a fixed interval and a loop exception only logs | A marker can enter the horizon between sweeps and readiness can remain stale after the loop fails |
| Retention accounting | Watch cleanup omits terminal event IDs; mock cleanup does unbounded expired-pin reconciliation and does not report tombstone/pin backlog | Hidden state can grow and operators cannot tell whether cleanup is catching up |
| Migration integrity | Discovery accepts an empty filesystem directory; rows record only version; no schema/package fingerprint is verified | Missing, duplicate, reordered, or changed migrations can pass startup |
| Deployment health | The image-level healthcheck always calls API `/health`; compose does not pass PostgreSQL to API/worker or depend on it | A healthy worker is marked unhealthy and configured durable history is unreachable |
| Hypotheses | No deterministic evidence establishes an admission-control failure, CAS contention result, or notifier uncertainty guarantee | Changing auth/rate/retry/delivery behavior now would be speculative |

### Affected Components and Interfaces

| Layer | Components | Required boundary change |
|---|---|---|
| Public API | `backend/main.py`, `backend/api/routes/watches.py`, `backend/api/dependencies.py`, `backend/api/client_identity.py` | Depend on immutable snapshots and application use cases; publish explicit frontend DTOs/errors and dedicated readiness |
| Application | `backend/services/watch_service.py`, `watch_recovery.py`, `notification_service.py`, `readiness.py`, new focused contract/effect modules | Consume committed facts, non-blocking projection, terminal delivery, shared temporal policy, and bounded pages |
| Models | `reservation.py`, `watch.py`, `watch_runtime.py`, orchestrator result schemas | Enforce UTC/order/version/state invariants on construction, reconstruction, and update |
| Live repositories | `watches.py`, `watch_decisions.py`, `watch_scripts.py` | Retain owner/revision, terminal delivery state, sorted scan indexes, bounded pages, and complete cleanup atomically |
| Mock repository/catalog | `mock_booking.py`, `mock_booking_scripts.py`, `data/venues.py` | Bound pin cleanup, account every retention class, distinguish closure from inheritance, and align calendar support |
| History/PostgreSQL | `watch_history.py`, `postgres.py`, migrations and package metadata | Revision-guarded writes, paged reads, migration checksums/set identity, schema/package verification, safe diagnostics |
| Workers | `monitor_watch.py`, dispatcher/queue composition, Celery signals | Use common topology/history/effect factories, retain retry semantics, and clean each initialized resource independently |
| Process lifecycle | API lifespan and worker bootstrap/shutdown | Stage composition transactionally; rollback and close all resources exactly once with primary-error preservation |
| Deployment | `Dockerfile`, `infra/docker-compose.yml`, one-shot health probe module | Use role-specific liveness/readiness and pass PostgreSQL to every history-using role |
| Tests/contracts | `tests/*`, generated OpenAPI/schema artifact | Deterministic clocks, barriers, fakes, generated traces, package/container checks, and exploration gates |

### Examples

- A Celery poll commits revision 8 `FOUND`, returns the unchanged worker result, but today emits no history write. The corrected worker offers the same revision-8 envelope as the API path without adding PostgreSQL errors to the retry tuple.
- A blocked fake history writer currently blocks watch creation before first dispatch. The corrected bounded publisher returns immediately; dispatch and the 201 response complete while projection readiness records backlog.
- A creation for `visitor-a` commits live state and its first PostgreSQL write fails. A later worker transition still carries `visitor-a` from private runtime metadata and repairs the row.
- Revision 11 `CANCELLED` reaches PostgreSQL before revision 10 `ACTIVE`. The conditional upsert ignores revision 10.
- History contains revision 4 `ACTIVE`, Redis retains revision 5 `BOOKED`, and another terminal watch exists only in history. The owner page returns the live `BOOKED` snapshot for the overlap and the history-only terminal item once, in immutable creation order.
- Recovery expires an attempt-exhausted watch. The same committed-facts handler offers the terminal projection and wakes the durable `EXPIRED` event; repeating recovery creates neither a second transition nor a second event.
- Startup creates Redis and PostgreSQL, then schema verification fails. The lifecycle ledger closes every acquired resource once in reverse dependency order and re-raises the sanitized schema failure even if one close also raises.
- Redis `INFO` raises after ping succeeds. The common policy returns `UNKNOWN`; the API never binds the atomic Redis repository and the worker refuses initialization rather than treating the server as non-clustered.
- `POST /api/watches` at `19:30:45` requests `19:30` today. The shared temporal policy rejects it; the orchestrator uses the same second-precise decision when converting extraction into clarification.
- A venue explicitly sets Sunday to closed. The explicit closure value remains `None`/`CLOSED` rather than being replaced by weekday hours; an omitted override still inherits.
- A notifier raises after a terminal commit. The caller receives the terminal result, while the durable event becomes `UNCERTAIN` or `RETRYABLE` according to an explicit attempt result; redelivery cannot silently erase it.
- An active index contains 100,000 members. Recovery reads one bounded stable page, bulk-fetches at most that page, renews authority before more work, and exposes the continuation/backlog instead of allocating all members.
- The packaged migration `0002` differs from the checksum recorded by a prior deployment. Startup fails before serving history even if the SQL table happens to exist.
- The worker container uses a finite worker-role probe rather than the API HTTP healthcheck; API and worker both receive the compose PostgreSQL URL and wait for healthy PostgreSQL when history is enabled.
- A forced five-round cancellation contention trace is treated only as exploration evidence. It does not become a new 409/503 or retry contract until the gate records a reproducible counterexample and an approved public outcome.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**

1. Header omission continues to create an unowned watch with the existing success status and public shape (3.1).
2. Every existing `PromptExecutionResult` status and field meaning remains unchanged; `WATCH_CREATED` continues to carry `watch_id` (3.2).
3. Public `Watch`, `WatchStatus`, and `WatchPollOutcome` fields/values remain exact; owner, revision, delivery, and projection metadata remain private (3.3).
4. Unscoped list and by-ID get/delete routes keep their status/body contracts and do not turn anonymous ownership into authorization (3.4).
5. The Milestone 3 live repository, claim/fence protocol, durable schedules, and retry decisions remain authoritative; PostgreSQL cannot veto or roll back them (3.5).
6. Projection failure after commit continues to leave the live result successful and cannot roll back live state (3.6).
7. When PostgreSQL and frontend origins are omitted, standalone API and `/health` operation remains supported (3.7).
8. Unconfigured/non-browser CORS behavior and configured exact-origin/no-credentials behavior remain unchanged (3.8).
9. Owner lists remain isolated by opaque identifier and preserve history-only terminal records after live cleanup (3.9).
10. Existing `/health` fields, HTTP behavior, top-level `status`, and `ready`/`degraded`/`unknown` vocabulary remain unchanged; dedicated role endpoints are additive (3.10).
11. Worker result shape, narrow Redis/Kombu retry classes and limits, runner serialization, optional imports, and idempotent cleanup semantics remain unchanged (3.11).
12. The backend remains independently deployable and the future frontend integrates only through HTTP/OpenAPI, never Python imports (3.12).
13. Existing Milestone 1–4 tests remain enabled and automated validation still requires no live browser, Redis, PostgreSQL, Celery broker, OpenAI, or provider (3.13).
14. The startup/logging sibling retains the exact stored watch-settings error and host-owned logging topology; the worker sibling retains its missing-settings invariant and retry partition (3.14).
15. Existing deadline policy, fencing, durable scheduling, shared mock state, outage backoff, recovery, readiness, and terminal-retention guarantees remain in force (3.15).
16. Valid existing temporal/model values continue to be accepted and serialize with the same public names and meanings (3.16).
17. Supported mock venue/date/time behavior retains deterministic slots, capacities, idempotency, atomic winners, holidays, and normalized provider errors (3.17).
18. In-memory and Redis repositories continue to produce equivalent decisions and observable watch outcomes for the same valid trace (3.18).
19. A successfully delivered terminal event remains user-visible at most once under task redelivery; delivery metadata stays private (3.19).
20. Public parse, booking, and watch endpoints continue to require no authentication; client IDs remain opaque scope values absent a separately approved exploration result (3.20).
21. Provider errors, prompt separation, optional-provider behavior, and deterministic injected doubles remain unchanged (3.21).

**Scope:**

```text
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT observable(CompleteBackend(input))
       = observable(CompleteBackendFixed(input))
END FOR
```

Relevant observations include HTTP status/headers/body, OpenAPI schemas, watch and runtime decisions, provider/booking behavior, schedules, retries, event delivery, resource ownership, sanitized diagnostics, readiness vocabulary, migration/package identity, and role deployment health. Additive private metadata and additive dedicated liveness/readiness interfaces are not changes to preserved public `Watch` or legacy `/health` values.

## Hypothesized Root Cause

The confirmed repository-backed causes are:

1. **Reversed dependency and awaited optional I/O**: `WatchService` imports a history protocol from `backend.db` and awaits it, confusing exception containment with latency isolation (1.1–1.2, 1.9).
2. **Ownership is attached to an attempt rather than authoritative state**: only creation's first projection receives the owner; runtime, atomic results, worker transitions, and recovery do not (1.3).
3. **Arrival order substitutes for source order**: history SQL has no source revision and owner query trusts projection over retained live state (1.4–1.5).
4. **Header/availability state collapse**: omitted and malformed client IDs collapse to `None`; disabled, failed, and empty history collapse to an empty list (1.6–1.7).
5. **Repository defaults leak through HTTP**: update ordering, a silent limit, concrete `app.state` persistence, and a bare list became frontend behavior (1.8–1.9).
6. **Configured and omitted capabilities collapse**: PostgreSQL/CORS failures are logged and disabled even when explicitly configured (1.10–1.12).
7. **Diagnostics use untrusted raw values**: connection URLs and driver/configuration messages are interpolated directly, and settings representations retain secrets (1.13, 1.20).
8. **Terminal effects are call-site behavior, not committed-state behavior**: poll paths directly notify/project, while recovery only applies repository expiry; event IDs have no delivery state (1.15, 1.24–1.25).
9. **Settings have no immutable process state**: dependencies can re-read ambient environment instead of resolving a retained startup value/error (1.16).
10. **Resource ownership is implicit**: acquisition and publication are interleaved, no rollback stack is registered at acquisition time, and teardown is one unguarded sequence (1.17, 1.19).
11. **Topology checks are advisory and split by role**: probe exceptions become `False` and worker composition bypasses the check entirely (1.18).
12. **Policy and model validation are fragmented**: orchestrator date/time checks are not reused by direct routes; models incompletely constrain UTC/order/status and unchecked `model_copy` updates bypass validators (1.21–1.22).
13. **Sentinel overloading and finite fixture drift**: `None` means both inherited and closed Sunday, while rolling acceptance and fixed calendar data have separate support boundaries (1.23).
14. **Operational logging was used as a notification payload**: the default notifier emits user reservation fields rather than a privacy-reviewed event summary (1.24).
15. **Nominal batch bounds occur after unbounded discovery**: `SMEMBERS`, whole-dict comprehensions, sequential per-record reads, and unbounded pin reconciliation precede batch slicing (1.26).
16. **Recovery scheduling assumes each pass is short and periodic sleep is sufficient**: leadership is not renewed during a pass, wake time ignores the next horizon boundary, and loop exceptions do not update readiness (1.27).
17. **Retention is split across partial indexes and counters**: event IDs, pins, tombstones, booking records, and class-specific backlog are not cleaned/accounted as one bounded policy (1.28).
18. **Migration identity is only a filename stem**: no non-empty required set, strict numeric order, duplicate detection, content checksum, schema fingerprint, distribution resource, or package-version assertion exists (1.29).
19. **Container health is image-generic rather than role-specific**: the worker inherits an API HTTP check and compose does not wire PostgreSQL to history users (1.30–1.31).
20. **Coverage is mostly sequential and line-oriented**: current tests do not prove cross-process composition, lifecycle interleavings, scan bounds, failure-point delivery, artifact identity, or semantic health (1.14, 1.32).
21. **Unproven hypotheses lack gates**: admission pressure, bounded CAS contention, and notifier uncertainty have plausible risks but no deterministic counterexample or approved public outcome (1.33). They remain hypotheses until the exploration process in this design passes.

## Correctness Properties

Property 1: Bug Condition - Complete Backend Correctness

_For any_ complete-backend input where `isBugCondition` returns true, the fixed system SHALL satisfy every paired clause among Requirements 2.1–2.33 using the shared authority, effects, lifecycle, validation, boundedness, integrity, and role-health mechanisms in this design.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.13, 2.14, 2.15, 2.16, 2.17, 2.18, 2.19, 2.20, 2.21, 2.22, 2.23, 2.24, 2.25, 2.26, 2.27, 2.28, 2.29, 2.30, 2.31, 2.32, 2.33**

Property 2: Preservation - Existing Public, Coordination, and Provider Behavior

_For any_ complete-backend input where `isBugCondition` returns false, the fixed system SHALL produce the same relevant observation as the original system, including every behavior protected by Requirements 3.1–3.21.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13, 3.14, 3.15, 3.16, 3.17, 3.18, 3.19, 3.20, 3.21**

Property 3: Bug Condition - Shared Non-Blocking Passive Projection

_For any_ committed create or transition in API, worker, or recovery composition, the fixed system SHALL offer the same owner-aware revisioned projection through a finite non-blocking port, and slow, failed, closed, or saturated projection work SHALL NOT delay or alter live commit, dispatch, result, or retry behavior.

**Validates: Requirements 2.1, 2.2**

Property 4: Bug Condition - Ownership, Freshness, Reconciliation, and Reachable Pages

_For any_ valid owner and any finite live/history dataset, the fixed system SHALL retain owner metadata independently of the first projection, reject malformed identity before side effects, keep the maximum authoritative revision, prefer live state on overlap, distinguish unavailable from empty history, and make every owned record reachable exactly once in deterministic creation order through the application query contract.

**Validates: Requirements 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9**

Property 5: Bug Condition - Explicit Configuration, Migration Startup, and Boundary Evidence

_For any_ omitted, valid, invalid, unreachable, missing-resource, or failing PostgreSQL/CORS configuration, the fixed system SHALL select only documented standalone or configured behavior, enforce the complete origin grammar, normalize startup failures, close acquired resources once, expose no PostgreSQL secret, and prove the boundary with deterministic contract tests.

**Validates: Requirements 2.10, 2.11, 2.12, 2.13, 2.14**

Property 6: Bug Condition - Recovery-Originated Terminal Effects

_For any_ eligible watch expired by recovery, the fixed system SHALL produce the same committed projection and unique durable terminal event as normal expiry, and repeated recovery or effect redelivery SHALL not create a duplicate terminal transition or delivery identity.

**Validates: Requirements 2.15**

Property 7: Bug Condition - Immutable Settings and Transactional Lifecycle

_For any_ environment mutation, startup failure position, initialized-resource subset, or throwing close callback, every request SHALL use the retained startup snapshot/error and the lifecycle ledger SHALL roll back or shut down all initialized resources in safe order exactly once while preserving the primary sanitized failure.

**Validates: Requirements 2.16, 2.17, 2.19**

Property 8: Bug Condition - Fail-Closed Topology and Worker Parity

_For any_ supported, unavailable, unsupported, unknown, or throwing Redis topology probe in any role, API and worker SHALL produce the same topology classification, SHALL bind atomic repositories only for a supported primary, and SHALL expose the configured role's fallback, readiness failure, or startup failure without changing the worker's established retry partition.

**Validates: Requirements 2.18, 2.19**

Property 9: Bug Condition - Comprehensive Diagnostic Redaction

_For any_ Redis/PostgreSQL URL containing user information, password, token, encoded secret, or query/fragment data and any parse/connect/migrate/cleanup failure, no settings representation, exception chain, log, health payload, HTTP body, or worker result SHALL contain a secret sentinel.

**Validates: Requirements 2.20**

Property 10: Bug Condition - Shared Temporal, Reconstruction, and Calendar Policy

_For any_ direct or orchestrated reservation input and any constructed, deserialized, or updated watch/runtime/result value, the fixed system SHALL apply one second-precise horizon/calendar decision and enforce supported schema, aware UTC ordering, bounded counters, valid transitions, and status/slot/booking consistency; explicit closure SHALL never be interpreted as inheritance.

**Validates: Requirements 2.21, 2.22, 2.23**

Property 11: Bug Condition - Privacy-Safe Recoverable Terminal Delivery

_For any_ committed terminal event and notifier failure point, operational observations SHALL contain only approved non-sensitive correlation data, the live result SHALL remain truthful, and private durable delivery state SHALL become delivered, definitely retryable, uncertain, or exhausted without an unrecoverable silent no-op.

**Validates: Requirements 2.24, 2.25**

Property 12: Bug Condition - Bounded Scans, Authority, Wakeups, and Retention

_For any_ finite repository/recovery/reconciliation/cleanup dataset, each pass SHALL read and retain no more than its configured page/work budget, every eligible item SHALL remain reachable through stable continuation, no side effect SHALL occur after leadership is unrenewable, dispatch SHALL occur within the defined horizon tolerance, loop failure SHALL degrade readiness, and repeated bounded cleanup SHALL account for and eventually drain every due retention class.

**Validates: Requirements 2.26, 2.27, 2.28**

Property 13: Bug Condition - Migration Set and Schema Identity

_For any_ packaged and applied migration histories, startup SHALL accept only a non-empty strictly ordered unique set whose applied checksums match, whose set identity/package assets are present, and whose resulting schema fingerprint satisfies the required history contract.

**Validates: Requirements 2.29**

Property 14: Bug Condition - Role-Specific Deployment Health

_For any_ API or worker deployment composition, liveness SHALL measure only that role's process, readiness SHALL fail when a capability required by its startup snapshot is semantically unready, the worker SHALL not depend on an API endpoint, and every configured history user SHALL receive and wait for PostgreSQL under the documented policy.

**Validates: Requirements 2.30, 2.31**

Property 15: Bug Condition - Complete Deterministic Backend Evidence

_For any_ confirmed scenario in Requirements 1.1–1.32, the validation suite SHALL exercise the relevant cross-module behavior with deterministic fakes, clocks, barriers, bounded traces, direct calls, or static artifacts and SHALL fail if it attempts a live external service.

**Validates: Requirements 2.32**

Property 16: Bug Condition - Exploration Before Policy Change

_For any_ admission, cancellation-contention, notifier-uncertainty, redelivery, or role-composition hypothesis, no established authentication, retry, cancellation, or delivery contract SHALL change unless a deterministic gate first reproduces the counterexample, records the minimal trace, and identifies an approved required public outcome.

**Validates: Requirements 2.33**

Property 17: Preservation - Public Watch and No-Authentication Contracts

_For any_ existing headerless creation, parse-and-book status, public Watch serialization, unscoped/by-ID route call, or opaque client-ID call, the fixed system SHALL preserve existing statuses, headers, fields, enum meanings, and lack of authentication/authorization semantics except for the explicitly corrected malformed-ID and owner-page contracts.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.20**

Property 18: Preservation - Milestone 3 Authority and Repository Equivalence

_For any_ valid live operation trace where passive effects do not decide the result, the fixed system SHALL preserve claim, fence, commit, marker, revision, retry, terminal-retention, and observable watch decisions in both in-memory and Redis repositories, including successful live results after projection failure.

**Validates: Requirements 3.5, 3.6, 3.15, 3.18**

Property 19: Preservation - Standalone, CORS, Owner Isolation, and Legacy Health

_For any_ supported standalone/configured-CORS deployment and owner-scoped read, the fixed system SHALL preserve standalone service, exact CORS policy, owner isolation, history-only terminal visibility, and every field/top-level meaning/vocabulary of legacy `/health`.

**Validates: Requirements 3.7, 3.8, 3.9, 3.10**

Property 20: Preservation - Worker and Sibling Hardening Contracts

_For any_ worker success, recognized Redis/Kombu failure, non-recoverable failure, concurrent runner call, optional-worker import, retained startup-settings error, or repeated shutdown signal, the fixed system SHALL preserve the sibling specs' result, retry, identity, serialization, logging-topology, and cleanup observations.

**Validates: Requirements 3.11, 3.14**

Property 21: Preservation - Independent Frontend Boundary and Regression Suite

_For any_ supported build or automated validation run, the backend SHALL remain independently deployable with no frontend source import, and all existing tests SHALL remain enabled and deterministic without a live external dependency.

**Validates: Requirements 3.12, 3.13**

Property 22: Preservation - Valid Models, Catalog, and Provider Safety

_For any_ input already satisfying the temporal/model/calendar contract and any supported mock/provider flow, the fixed system SHALL preserve acceptance, public serialization, deterministic slot/capacity/idempotency behavior, holiday results, safe provider normalization, and injected-double operation.

**Validates: Requirements 3.16, 3.17, 3.21**

Property 23: Preservation - Successful Terminal Delivery Deduplication

_For any_ terminal event whose notifier reports successful delivery, every physical task redelivery SHALL resolve through the same private event identity and SHALL produce no second user-visible successful delivery.

**Validates: Requirements 3.19**

## Fix Implementation

### Architectural Direction

```text
Future frontend (generated OpenAPI client; HTTP only)
                         |
                         v
FastAPI routes + public request/page/error DTOs
                         |
            application use-case contracts
          /              |                 \
         v               v                  v
 Watch lifecycle    OwnedWatchQuery    Health/temporal policy
         |            /        \                |
         v           v          v               v
 Authoritative   live page    history page   immutable snapshot
 Live_Watch_Store   reader       reader
         |
         +-- atomic CommittedFacts -----------------------+
         |                                                |
         +--> ProjectionPublisher.offer() --> bounded pump+--> PostgreSQL
         |             ^                                  |
         |             +--- bounded reconciliation pages -+
         |
         +--> durable TerminalEventRecord --> claimed delivery dispatcher
                                                     |
                                                     v
                                               NotificationPort

API composition root and worker composition root
        \________ common settings/topology/migration/contracts ________/
                         LifecycleLedger
```

Concrete persistence is confined to composition and adapters. Application services import contracts from an application-level module (for example `backend/application/contracts.py` and focused `projection.py`, `terminal_effects.py`, and `lifecycle.py` modules); they do not import `backend.db.repositories.*` protocols. HTTP routes import only application use cases and API DTOs.

### Sibling-Spec Ownership and Composition

| Existing owner | Remains owned there | This design's composition rule |
|---|---|---|
| `app-logging-startup-error-visibility` | First-call logging initialization, host handler/formatter preservation, one visible retained watch-settings error | `configure_application_logging()` remains the first lifecycle action. New diagnostics use sanitized values but do not add/replace handlers, levels, formatters, or duplicate the retained error log. |
| `watch-route-worker-retry-hardening` | `get_watch_service` missing-settings invariant, exact Redis/Kombu retry tuple, `max_retries=3`, countdown, traceback behavior, runner lock, optional worker import | New settings dependencies use the same retained-state rule. Projection/notification failures never enter the retry tuple. Worker resource ownership extends cleanup to each initialized resource without changing runner serialization or lazy optional imports. |
| `milestone-3-production-path-hardening` | Live authority, deadline policy, claims/fences, durable markers, dispatch generations, recovery baseline, shared mock state, outage backoff, retention, readiness vocabulary | Owner/event/page metadata is added atomically beside those decisions. No PostgreSQL/notifier call enters a Lua script or decision. Existing decision enums remain unless an exploration gate separately approves a new contention result. |
| Completed Milestone 4 tasks 1–7 | Existing public Watch/parse contracts, optional standalone mode, CORS allowlist shape, history intent | The corrected owner page and errors are explicit HTTP contracts; all earlier public shapes remain preserved where Requirements 3.x require them. |

### Mechanism 1: Shared Application Contracts and Frontend HTTP Schemas

| Design obligation | Decision |
|---|---|
| Current root cause | Routes reach `app.state` repositories and `WatchService` imports a persistence protocol; repository limits/order and exception types leak into HTTP. |
| Affected components/interfaces | New application contracts; API dependency module; watch routes; main parse-and-book route; generated OpenAPI contract. |
| Invariants | Routes depend on substitutable use cases; internal metadata never enters public DTOs; HTTP schemas are explicit and versioned; the frontend imports generated HTTP types only. |
| Failure and cleanup | Application errors are translated once to stable sanitized HTTP errors; no route constructs/owns persistence resources. |
| Public-contract impact | `Watch` and legacy routes remain unchanged. `/api/watches/mine` becomes a paged wrapper; malformed ID/pagination/temporal and unavailable-history errors use documented schemas. Dedicated health endpoints are additive. |
| Ordering/concurrency | Dependency resolution validates snapshot and identity before any parse/create/read side effect; use cases are safe to replace with deterministic fakes. |
| Deterministic tests | Dependency overrides, no-repository-import static checks, OpenAPI snapshots, generated-client compile/type checks, and error/status matrices. |

Application contracts include:

```text
TYPE CommittedFacts
  watch: Watch
  runtime: WatchRuntime_v3
  source_revision: integer
  owner_client_id: string OR null
  terminal_event_id: string OR null
END TYPE

INTERFACE WatchLifecycle
  create(..., owner_client_id) -> Watch
  cancel(watch_id) -> Watch OR null
  poll_window(...) -> WatchPollResult
END INTERFACE

INTERFACE OwnedWatchQuery
  execute(owner_client_id, cursor, limit) -> OwnedWatchPage
  RAISES HistoryUnavailable, InvalidCursor
END INTERFACE

INTERFACE TemporalPolicy
  evaluate(query_or_extraction, local_now) -> Accepted(normalized) OR Violation(code)
END INTERFACE
```

Frontend-facing DTOs live in a focused API contract module and are emitted in OpenAPI:

- `PublicError { error: { code, message, retryable } }`
- `OwnedWatchItem { watch: Watch, tracking_state: LIVE | HISTORY_ONLY }`
- `PageInfo { limit, has_more, next_cursor }`
- `OwnedWatchPage { items, page }`
- existing `ParseRequest`, `ReservationIntent`, `PromptExecutionResult`, `AvailabilityQuery`, and `Watch` schemas

Corrected/new errors are controlled codes: `INVALID_CLIENT_ID`, `INVALID_PAGINATION`, `HISTORY_UNAVAILABLE`, `INVALID_TIME_WINDOW`, `RESERVATION_TIME_ELAPSED`, and `OUTSIDE_SUPPORTED_HORIZON`. They never echo a header, cursor, URL, driver message, reservation payload, or internal exception. Existing preserved endpoints may keep their established `detail` form; the OpenAPI client normalizes both documented forms rather than guessing repository behavior.

`GET /api/watches/mine?limit=&cursor=` returns `OwnedWatchPage`. Default limit is 50 and maximum is 100. Omitted client ID returns an empty page without a storage call; a present malformed ID returns 422 before side effects; a valid ID with unavailable history returns sanitized 503. The future frontend consumes the generated OpenAPI artifact or generated TypeScript client and never imports backend Python.

### Mechanism 2: Authoritative Metadata, Bounded Projection, Reconciliation, and Pagination

| Design obligation | Decision |
|---|---|
| Current root cause | Owner is not retained, writes await PostgreSQL, worker/recovery omit history, SQL is arrival-ordered, and owner reads trust one unpaged projection. |
| Affected components/interfaces | `WatchRuntime`, atomic result types and scripts, live owner/scan indexes, `WatchService`, recovery, API/worker composition, projection pump, history repository, owner query. |
| Invariants | Live state/revision is authoritative; owner is private and immutable once non-null; projection is passive; per-watch history revision never decreases; live wins overlap; page key is immutable `(created_at, watch_id)`. |
| Failure and cleanup | Offer overflow/closed/write failure changes only projection readiness/backlog; bounded reconciliation retries retained live snapshots. PostgreSQL outage never rolls back live state. Projection pump drains only to a shutdown deadline, then records remaining backlog. |
| Public-contract impact | Public Watch is exact. Owner page gains explicit page/tracking metadata and stable unavailable errors. Legacy unscoped list remains a JSON array. |
| Ordering/concurrency | Atomic create/commit/transition returns owner+revision from the same operation; local coalescing keeps newest revision; SQL compare enforces global order across API/worker/recovery and duplicates. |
| Deterministic tests | Blocked writer barriers, capacity bursts, API/worker/recovery composition parity, first-write owner failure, revision permutations, generated overlap/page traversals, and shutdown-drain bounds. |

`WatchRuntime_v3` adds `owner_client_id` with the existing bounded opaque-token grammar. Version 2 sidecars migrate explicitly to version 3 with `owner_client_id=None`; legacy public-only watches migrate through the existing Milestone 3 path and then to v3. Unknown future versions fail closed. Owner is written atomically with `create_with_schedule`, copied by every validated runtime transition, and removed only by normal bounded retention. Atomic `CreateResult`, `CommitResult`, and `TransitionResult` expose `CommittedFacts` privately.

`ProjectionPublisher.offer(envelope)` is synchronous, constant-time, non-awaiting, exception-contained, and returns `ACCEPTED`, `COALESCED`, `REJECTED_FULL`, or `REJECTED_CLOSED`. One finite map/deque per process stores at most one newest pending envelope per watch. A same-watch newer revision replaces the pending envelope without increasing occupancy; stale/equal revisions are ignored. A new watch at capacity is rejected immediately and records degraded readiness/backlog.

A common history-runtime factory is called by API and every worker child. The API uses its event loop; the worker uses a lifecycle-owned background loop/thread so a Celery task only performs the thread-safe offer and retains its synchronous result/retry behavior. Configured history initialization failure fails the relevant role startup. Runtime projection failures remain outside the worker retry classifier.

The live operation order is:

```text
atomic live commit (including owner/revision/event facts)
  -> publish existing durable schedule/return existing live decision
  -> offer projection without waiting
  -> wake terminal delivery when an event exists, without waiting
  -> return truthful live result
```

For active successors, the existing durable marker is committed atomically before best-effort queue publication. For terminal transitions, the event record is committed atomically before any delivery attempt. PostgreSQL and notifier completion order cannot change the live result.

History rows add `source_revision BIGINT NOT NULL`. The conditional upsert:

1. Inserts a missing watch/revision.
2. Replaces public state only when `excluded.source_revision > current.source_revision`.
3. Treats equal revision as idempotent; it may fill a null owner only with matching retained metadata.
4. Preserves the first non-null owner; a conflicting non-null owner is a sanitized invariant failure and never reassigns the row.
5. Never changes immutable `watch_id` or `created_at`.

Projection reconciliation uses the same bounded stable live-page protocol as recovery. It re-offers the current maximum revision and therefore repairs dropped/failed envelopes while a record remains retained. It does not promise recovery after the configured live-retention boundary; backlog age and lost-after-retention counts make that finite limitation explicit rather than retaining live state forever.

`OwnedWatchQuery` requires an available history reader for a valid owner so it never presents an incomplete live-only page as complete. Live and history readers use the exclusive cursor key `(created_at DESC, watch_id DESC)` and fetch at most `limit + 1` plus a fixed overlap allowance. A two-way merge deduplicates by `watch_id`; the live snapshot wins every collision. History-only terminal rows remain after live cleanup. The opaque cursor is URL-safe, versioned, integrity-checked, contains no owner/secret, and preserves the traversal boundary. Stable data traversal is complete and duplicate-free; records created after page one are newer than its boundary and appear on a new traversal.

### Mechanism 3: Recovery-Originated Terminal Effects and Idempotent Delivery State

| Design obligation | Decision |
|---|---|
| Current root cause | Recovery calls `expire_if_eligible` without the poll path's projection/notifier calls; direct post-commit notification has only an event-ID gate and no outcome state. |
| Affected components/interfaces | Atomic terminal scripts/results, `WatchService`, `RecoveryCoordinator`, new terminal-effect repository/dispatcher, notification port, readiness/cleanup. |
| Invariants | Each terminal transition has at most one deterministic event ID; recovery and normal transitions feed the same committed-facts handler; public Watch excludes delivery state; cancellation remains notification-free. |
| Failure and cleanup | Notification failure never changes the live result. Durable state records definite retry, uncertainty, or exhaustion. Leases expire; bounded recovery reclaims work. Event/payload cleanup follows a finite retention policy and reports unresolved backlog. |
| Public-contract impact | No Watch/result field changes. Failures become operational readiness/backlog, not ambiguous API/worker failure. Notification logs become privacy-safe. |
| Ordering/concurrency | Event creation is atomic with terminal commit; delivery requires a finite claim lease and compare-owner completion; duplicate task/recovery wakes share the event ID. |
| Deterministic tests | Normal-vs-recovery expiry differential tests; concurrent event claims; failure before/after side effect; lost response; receipt/operator resolution; lease takeover; redelivery; bounded dead-letter retention; privacy sentinels. |

The live repository stores one `TerminalEventRecord` for `FOUND`, `BOOKED`, or `EXPIRED` as part of the same atomic terminal transition. It contains only private bounded data needed by a notifier, a random/deterministic non-secret correlation/event ID, revision, timestamps, state, attempt count, next-attempt time, claim owner/token/expiry, and controlled error category. Cancellation creates no event.

```text
PENDING/RETRYABLE --claim--> IN_FLIGHT
IN_FLIGHT --definitely delivered--> DELIVERED
IN_FLIGHT --definitely not sent--> RETRYABLE(next_attempt_at)
IN_FLIGHT --outcome unknowable--> UNCERTAIN
UNCERTAIN --receipt/idempotency reconciliation says sent--> DELIVERED
UNCERTAIN --reconciliation says definitely absent--> RETRYABLE
UNCERTAIN --authorized resolution declines replay--> EXHAUSTED
RETRYABLE --attempt ceiling/deadline--> EXHAUSTED
expired IN_FLIGHT lease --> PENDING or UNCERTAIN according to the approved transport policy
```

`TerminalEffects.observe(committed_facts)` performs only non-blocking projection and delivery wakeups. Both `WatchService` and `RecoveryCoordinator` call it after an `APPLIED`/`COMMITTED` result. If recovery repeats and the repository returns `NOOP`, it does not manufacture another event; the bounded pending-event scanner still recovers an existing undelivered event.

`NotificationPort.deliver(event_id, private_payload)` must return `DELIVERED`, `DEFINITELY_NOT_DELIVERED`, or `UNKNOWN`; an unclassified throw maps to `UNKNOWN`. A transport that supports idempotency receives the stable event ID and may safely retry after an uncertain response. Every enabled transport that can produce `UNKNOWN` must also supply a `DeliveryRecovery` adapter that can query an idempotency receipt/provider status, or an authorized non-HTTP operator use case that resolves the event by compare-and-set to `DELIVERED`, `RETRYABLE`, or `EXHAUSTED`. While `UNCERTAIN`, automatic replay is prohibited but the event remains actionable and readiness remains degraded. At a configured finite resolution deadline, unresolved state becomes `EXHAUSTED`, its reservation payload is erased, and a compact non-sensitive dead-letter tombstone remains for a second finite audit-retention interval before bounded cleanup. A transport lacking idempotency or a recovery adapter cannot be enabled until the notifier exploration gate approves an explicit guarantee. This preserves current successful at-most-once user-visible behavior while making failure recoverable and bounded.

The default logging notifier is operational observability, not a user reservation transcript. It records an approved event type, outcome category, and non-sensitive event correlation only. It omits watch/client identity, venue, date, party size, credentials, query text, and slot/booking details. Sentinel tests inspect rendered records and structured extras.

### Mechanism 4: Immutable Startup Snapshots and Transactional Lifecycle

| Design obligation | Decision |
|---|---|
| Current root cause | Settings can be reparsed during requests; resources are published as they are acquired; startup has no rollback stack; shutdown exceptions abort later cleanup. |
| Affected components/interfaces | `create_app`, lifespan, API dependencies, settings parsers, common composition factory, worker bootstrap/signals, lifecycle ledger. |
| Invariants | Environment is captured once per process/app instance; each settings family retains exactly one value or error; no request parser fallback; resources are registered immediately after acquisition and published only after the graph verifies. |
| Failure and cleanup | Startup rollback runs all registered closes in reverse dependency phases and re-raises the primary sanitized failure. Shutdown attempts every close, logs controlled cleanup categories, and is idempotent/exactly once. |
| Public-contract impact | Existing retained configuration 503 identity/wording remains where owned by sibling specs; dedicated readiness reflects the same snapshot. No successful route changes. |
| Ordering/concurrency | One composition lock/transaction builds a local graph; publication to app/worker state is atomic after verification. Ledger closure is lock-protected and stateful (`OPEN`, `CLOSING`, `CLOSED`). |
| Deterministic tests | Environment mutation after startup, every acquisition failure position, throwing closes, repeated/concurrent shutdown signals, publication-before/after checks, and exact primary-error identity. |

`create_app` captures an immutable environment mapping. CORS is parsed from that capture before middleware construction; lifespan parses watch, application, PostgreSQL, and role settings from the same capture exactly once. A generic retained state is used:

```text
StartupValue[T] = Ready(value: T) | Failed(error: ConfigurationError)
StartupSnapshot = frozen { watch, application, postgres, cors, role, origin_metadata }
```

`get_orchestrator`, `get_watch_service`, owner query dependencies, health, and composition inspect only `StartupSnapshot`. They re-raise the retained sanitized error or an internal invariant error; they never call `from_environment()` again. Existing exact retained `WatchSettings` error behavior from the logging/worker siblings is preserved.

Composition is staged locally:

1. Invoke the sibling-owned logging initializer.
2. Build the immutable snapshot and retain errors.
3. Create fallback application values without publishing replaceable resources.
4. Classify Redis topology and select the role-supported store/queue policy.
5. Open PostgreSQL, immediately register its close with the ledger, then verify/apply the packaged migration set and schema before publishing any history capability.
6. Build projection/delivery pumps, repositories, queues, provider clients, and recovery; register each resource immediately after acquisition and before its next fallible initialization step.
7. Start finite background loops only after their dependencies exist.
8. Publish one verified `ProcessRuntime` to app/worker state.

The `LifecycleLedger` uses dependency phases: stop ingress/wake producers; stop recovery/dispatch; stop accepting projection/delivery work and bounded-drain; close queues/notifiers/orchestrator; close PostgreSQL/Redis clients and loops. Within a phase it is LIFO. Every callback has an internal once guard. A cleanup exception is recorded as `{resource_kind, phase, exception_class}` with no raw message/URL; all later callbacks still run. During startup rollback, cleanup failures never replace the primary failure. During ordinary shutdown, they are reported after all cleanup completes.

### Mechanism 5: One Redis Topology Policy and Worker Resource Ownership

| Design obligation | Decision |
|---|---|
| Current root cause | API topology exceptions become “not clustered,” worker has no equivalent gate, and cleanup assumes runner initialization precedes Redis initialization. |
| Affected components/interfaces | Redis settings/URL parser, connection factory, topology inspector, `backend/workers/celery_app.py`, API composition, worker parent/child bootstrap, worker cache/resource ledger, health/readiness. |
| Invariants | Only a directly addressed supported standalone/primary endpoint may back atomic multi-key scripts; API and worker use one classifier; unknown never means supported; each initialized worker resource has an independent owner/close path. |
| Failure and cleanup | API may use the documented memory/asyncio standalone fallback but records why an explicitly requested distributed capability is unready; a worker that requires Redis/broker fails startup. Redis closes once even if no service runner exists. |
| Public-contract impact | API fallback request behavior remains; readiness becomes honest. Worker result/retry dictionary and retry classes remain exact. |
| Ordering/concurrency | Probe is finite and precedes repository/queue binding. Worker close holds the sibling-owned lock, marks closing once, stops pumps, closes initialized clients, then runners. |
| Deterministic tests | Supported/cluster/unknown/throwing probes in both roles; explicit-vs-omitted fallback; every cache initialization subset; concurrent Celery/atexit shutdown; retry-partition regression. |

`RedisTopologyPolicy.inspect(client, safe_endpoint)` performs finite reachability and topology checks and returns one controlled decision. Supported transport schemes remain those deliberately supported by both redis-py and the configured worker transport; Redis Cluster and Sentinel discovery/failover remain unsupported until separately designed. Probe exceptions return `UNKNOWN`, never `SUPPORTED_PRIMARY`.

Role policy is explicit after the shared classification:

| Decision | API role | Worker role |
|---|---|---|
| `SUPPORTED_PRIMARY` | Bind Redis repository and selected queue | Bind Redis repository/broker after the same gate |
| `UNAVAILABLE` | Supported local fallback; explicit Redis intent degrades dedicated readiness | Fail worker startup because no process-local distributed-worker authority exists |
| `UNSUPPORTED` | Never run scripts; local fallback plus degraded readiness | Fail startup |
| `UNKNOWN` | Never run scripts; local fallback plus degraded readiness | Fail startup |

Celery configuration is part of the same immutable worker transaction rather than an independent import-time environment read. At module import, `backend/workers/celery_app.py` creates exactly one `WorkerBootstrap` from a frozen copy of the environment; that bootstrap parses/retains the `StartupSnapshot` and constructs the Celery application with the snapshot's broker URL and timezone. No other worker module calls `os.getenv` or reparses settings. Constructing the Celery object performs no connection. A parent-process pre-consumer bootstep runs the finite topology gate before Celery opens its consumer or any Redis-backed service, publishes the retained topology result for child processes, and aborts worker startup on every non-`SUPPORTED_PRIMARY` decision. Each prefork child receives the immutable serialized safe snapshot/topology generation and validates that generation before building task resources. This preserves the `celery -A backend.workers.celery_app worker` command and optional import behavior while preventing a consumer from racing ahead of the common gate.

Worker resources are registered separately: settings snapshot, projection runtime, notification runtime, Redis client, service, and runner. If an older/lazy path has initialized Redis without the persistent runner, cleanup creates a temporary finite cleanup runner only because a resource exists, awaits `aclose`, closes that temporary runner, and marks Redis closed. Empty caches construct nothing. Repeated Celery and `atexit` signals are no-ops after the first close. The existing persistent runner lock and exact recoverable Redis/Kombu tuple are unchanged.

### Mechanism 6: Comprehensive Connection-String and Diagnostic Redaction

| Design obligation | Decision |
|---|---|
| Current root cause | Raw URLs and exception messages are interpolated into settings errors/startup logs, and settings reprs expose DSNs. |
| Affected components/interfaces | Configuration values, safe URL parser, PostgreSQL/Redis factories, migration/lifecycle errors, logs, health, HTTP error translation, worker diagnostics. |
| Invariants | No observable diagnostic receives a raw connection string or raw untrusted driver message; safe endpoint context contains only approved scheme class, host/socket class, valid port, and database name. |
| Failure and cleanup | Low-level causes are classified to controlled codes; raw exception text is not chained into public/logged failures. Cleanup logging uses resource/exception class only. |
| Public-contract impact | Errors remain actionable by setting/capability/category but never disclose credentials or reservation data. |
| Ordering/concurrency | Redaction happens during parse into a secret-bearing value object; callers cannot accidentally obtain a diagnostic repr of the raw value. |
| Deterministic tests | Generated percent-encoded users/passwords, token query keys, fragments, malformed ports, nested exceptions, repr/log/health/HTTP/worker sentinel scans. |

Connection settings use a secret wrapper with `repr=False` and a `SafeEndpoint` derived without user information, query, or fragment. `str`/`repr` of settings and startup snapshots display only safe endpoint context or `<configured>`. Configuration errors identify the setting and controlled reason, not its value. Known connection/migration failures are raised `from None` as a sanitized `StartupFailure(code, safe_endpoint)`; internal raw exceptions may be counted by class but are not formatted into logs or responses.

All Redis/PostgreSQL diagnostic call sites use structured controlled fields. The design does not alter sibling-owned logging handlers, filters, levels, or formatters. Redaction is achieved before log-record construction, with sentinel tests as the enforcement boundary.

### Mechanism 7: Shared Temporal Policy, Validated Reconstruction, and Calendar Boundaries

| Design obligation | Decision |
|---|---|
| Current root cause | Direct and orchestrated validation differ; same-minute checks discard seconds; model invariants are partial and `model_copy(update=...)` skips validation; closure/calendar sentinels are ambiguous. |
| Affected components/interfaces | `AvailabilityQuery`, `Watch`, `WatchRuntime`, execution/poll result models, update helpers, `IntentValidator`, direct watch route, venue catalog, mock adapter. |
| Invariants | One zoned fakeable clock and policy decides horizon/time; persisted datetimes are aware UTC and ordered; runtime versions are explicit; counters/transitions/result shapes are valid on every construction path; closure and inheritance are distinct. |
| Failure and cleanup | Invalid API inputs map to stable 422; invalid persisted values fail closed for that record, are pruned/quarantined through bounded recovery, and degrade readiness rather than being reinterpreted. |
| Public-contract impact | Valid public schemas/values remain unchanged. New direct invalid cases receive documented 422 codes. No private version/owner state is exposed. |
| Ordering/concurrency | Transition builders validate the fully merged model before repository commit; deserialization validates before a record enters decisions; UTC conversion occurs at boundaries, not after comparison. |
| Deterministic tests | Fake clocks at seconds/DST/horizon boundaries; direct-vs-orchestrator decision matrix; JSON/update mutation generators; unknown runtime versions; Sunday inherit/closed and calendar-boundary cases. |

`ReservationPolicy` consumes local zoned `now`, reservation date/time/window, `MAX_DAYS_AHEAD`, slot grid, and `CalendarPolicy`. It returns an accepted normalized value or a typed violation. The orchestrator converts a violation to its preserved clarification contract; direct API converts the same violation to stable 422. A same-day slot is elapsed when its full local datetime is not strictly after `now`; seconds and microseconds are included before rounding to the next slot boundary. Reversed windows are structurally invalid. Accepted dates satisfy both the rolling horizon and calendar support.

Calendar support is one fixed policy: deterministic holiday closure/sellout data is generated on demand for every calendar year intersecting the accepted interval `today <= target_date <= min(today + 365 days, date.max)`. The rolling horizon is therefore the only support boundary; there is no independent terminal `SUPPORTED_YEARS` acceptance rule. Requests outside that interval return `OUTSIDE_SUPPORTED_HORIZON`, and every accepted day has complete closure/sellout data before venue evaluation. Existing 2024–2031 outputs are snapshot-preserved while boundary tests move `today` across the former terminal year.

Weekly hours use an explicit inheritance sentinel distinct from closed, for example `INHERIT` versus `CLOSED`/`None`. Omitting an override inherits; explicitly closed Sunday remains closed. Catalog construction validates exactly seven entries and valid opening/closing values.

Model rules include:

- all persisted `Watch`, `WatchRuntime`, booking, and event timestamps are aware UTC;
- `created_at <= updated_at`, `created_at < expires_at`, optional check/schedule/delivery times have defined ordering, and terminal watches have no next schedule;
- `schema_version` is a supported literal after explicit migration; unknown versions fail closed;
- revisions/counters remain bounded and non-negative; attempts do not exceed maximum;
- `BOOKED` requires a matching booking and slot, `FOUND` requires slots and no booking, and statuses that cannot contain booking data reject it;
- poll/execution outcomes require their corresponding watch/slot/booking/watch-ID combinations;
- repository transition helpers cannot decrease revision or change immutable watch identity/creation time.

Unchecked `model_copy(update=...)` is replaced at state transitions by `evolve_watch`/`evolve_runtime`, which merge Python values and call full `model_validate`. Reconstruction always uses the same validators.

### Mechanism 8: Bounded Repository/Recovery Work, Authority, Wakeups, and Retention

| Design obligation | Decision |
|---|---|
| Current root cause | Whole sets/dicts are read before slicing, per-record Redis reads are sequential, pin reconciliation is unbounded, leadership can expire mid-pass, fixed sleep misses horizon transitions, and cleanup accounting is partial. |
| Affected components/interfaces | Live repository page protocols/indexes, owner/projection/recovery scanners, mock repository/scripts, dispatcher, recovery scheduler, readiness tracker, cleanup reports. |
| Invariants | Every pass has page/item/round-trip/time budgets; continuation is stable; bulk reads are bounded; no leader-only mutation occurs without authority margin; all cleanup classes have due indexes and backlog counts. |
| Failure and cleanup | Page failure retains cursor/backlog for retry; authority loss stops before the next side effect; loop exceptions immediately degrade readiness; cleanup failures retain data and resume in bounded later passes. |
| Public-contract impact | Legacy unscoped list remains a JSON array and may be streamed from bounded pages; owner pages expose cursors. Readiness becomes semantically accurate through additive dedicated endpoint behavior. |
| Ordering/concurrency | Sorted scan indexes use immutable keys; new items do not corrupt an existing continuation. Leader renewal occurs before each page and before work exceeding the safety margin. Per-window claims remain the final dispatch fence. |
| Deterministic tests | Instrumented large indexes, call/memory budgets, dual-write/resumable legacy backfill, legacy-event suppression, cursor mutation traces, token-fenced lease-expiry barriers, fake wake scheduler, loop exceptions, and per-class repeated-drain oracles. |

Repository protocols replace whole-index reads with bounded pages:

```text
scan_watches_page(kind, cursor, limit) -> Stable_Page[Watch]
scan_recovery_page(cursor, limit) -> Stable_Page[RecoveryCandidate]
scan_projection_page(cursor, limit) -> Stable_Page[CommittedFacts]
scan_terminal_events(cursor, due_before, limit) -> Stable_Page[TerminalEventRecord]
cleanup_page(kind, due_before, limit) -> CleanupClassResult
```

Redis uses ordered indexes with an immutable composite member/key and bounded range operations; it bulk-fetches at most one page through a fixed number of pipeline calls. In-memory implementations maintain corresponding ordered indexes rather than constructing whole due lists. The legacy unscoped HTTP array is encoded as a bounded-page JSON stream so repository and server working memory are bounded while clients retain the same array shape.

Existing Redis data upgrades through an explicit bounded index epoch. Following the Milestone 3 mixed-worker gate, old workers are drained before the new release starts. New code immediately dual-writes removals and additions to the legacy sets and the new ordered owner/recovery/projection/event indexes. A leader-owned resumable backfill stores `{epoch, SSCAN cursor, examined, added, remaining}` and reads each legacy set with bounded `SSCAN COUNT`; duplicate observations are idempotent and no whole `SMEMBERS` call is allowed. New writes are already present in both schemes, and deletes remove both, so one persisted cursor traversal after old writers stop can complete safely. Until the epoch is complete, normal bounded scanners read the ordered page plus bounded legacy cursor pages and deduplicate by watch ID; readiness reports `migration_backlog` and cannot claim complete recovery. Completion atomically flips the epoch only after all required indexes report a finished traversal. Legacy terminal event IDs contain no payload or delivery evidence, so backfill creates a private `LEGACY_SUPPRESSED` tombstone tied to the terminal record's retention deadline and never replays it; this conservatively preserves successful-delivery deduplication while allowing bounded event cleanup.

`AuthorityGuard` tracks a compare-owner lease epoch/token and expiry using an injected monotonic clock. Every leader-only repository mutation, including candidate repair, cleanup, and dispatch-claim acquisition, checks that token in the same atomic operation. Before each page or external publish, the guard renews when necessary and requires `hard_operation_timeout + cleanup_margin < remaining_lease`; invalid configuration fails startup. Queue publication executes under that hard timeout only after an authority-checked per-window dispatch claim. On timeout, cancellation, or token loss the pass performs no further leader work, does not mark the dispatch accepted, and leaves the existing dispatch generation/lease for the Milestone 3 recovery rule; an unknowable broker acceptance may cause another physical publication but cannot create another logical provider cadence. A completion arriving after lease expiry cannot apply a leader-only repository mutation because its token is stale. Tests pause both repository and broker operations across expiry and assert token rejection, no post-loss continuation, and preserved per-window fencing.

The recovery scheduler computes the next wake as the minimum of:

- the next marker's `scheduled_for - dispatch_horizon`;
- authority renewal deadline minus margin;
- immediate continuation when a page/dispatch/cleanup backlog remains;
- a finite horizon-tolerance fallback poll.

Marker commits publish a best-effort wake signal; the finite fallback tolerance is the guarantee if a cross-process signal is lost. Fake-clock tests define and enforce the maximum tolerance. Any scheduler-loop exception records degraded recovery readiness before retry; a later complete pass is required to recover readiness.

Cleanup returns a structured report for terminal documents, runtime/fence/claim/dispatch/index state, terminal events/payloads, expired pins and pin-owner indexes, idle slots, booking confirmations, tombstones, and projection/delivery reconciliation metadata. Each class reports `examined`, `removed`, `remaining_due`, and `oldest_due_age`; the aggregate reports bounded work consumed. Pins are reconciled with a `LIMIT`, not an unbounded expired range. Terminal event data is retained until delivery is resolved or a finite retry/dead-letter deadline is reached, then removed through the same bounded accounting. Repeated cleanup is idempotent; native TTL remains only a backstop.

### Mechanism 9: Migration Set Identity, Checksums, Schema, and Package Verification

| Design obligation | Decision |
|---|---|
| Current root cause | Filesystem discovery may be empty, versions are free-form stems, applied rows store no checksum, and startup never verifies final schema or installed resources. |
| Affected components/interfaces | `backend/db/postgres.py`, migration resource package, SQL tracking schema, build metadata, API/worker startup verifier, artifact tests. |
| Invariants | Required set is non-empty, numeric versions are unique/strictly increasing, entry checksum is immutable once applied, applied history is a valid prefix, package identity and schema fingerprint match. |
| Failure and cleanup | Any discovery/history/schema/package mismatch is a sanitized startup failure; an opened pool remains ledger-owned and closes exactly once. No role serves history after partial verification. |
| Public-contract impact | None on success; configured history fails startup rather than appearing empty/degraded when integrity is unknown. |
| Ordering/concurrency | Discovery/validation precedes SQL; advisory transaction lock protects compare/apply/record/verify; API and worker races converge on the same set. |
| Deterministic tests | Missing/empty resources, malformed/duplicate/reordered versions, changed SQL, valid append-only prefix upgrade, unknown applied rows, rollback, concurrent fake runners, schema mismatch, wheel/container resource and distribution-version checks. |

Migrations are loaded with `importlib.resources` from a real package and included explicitly in setuptools package data. Each entry is `(numeric_version, canonical_name, sha256(sql_bytes))`; the `MigrationSet.id` is SHA-256 over the canonical ordered entries and expected application schema version. Discovery rejects empty, missing, duplicate, malformed, non-increasing, or unexpected required entries before connecting/serving.

`schema_migrations` stores immutable per-entry provenance: version, canonical name, checksum, the package version that first applied that entry, and applied time. A separate singleton `schema_state` row stores the latest successfully verified migration version, the checksum of that applied prefix, the current complete `MigrationSet.id`, expected schema version, and verifier time. Existing version-only installations are upgraded under the same lock only after their resulting known schema is verified; then each known historical entry is bound to its checksum.

Append-only upgrade comparison is explicit: applied rows must equal a checksum-valid prefix of the packaged entries; the previous `schema_state` prefix checksum must match that same prefix; new suffix entries may then apply in order. After schema verification succeeds, startup atomically advances the singleton to the new complete set identity. A package-version change with identical migration bytes is allowed, while a changed historical checksum, unknown applied version, non-prefix history, missing resource, or running-package metadata mismatch fails. This accepts ordinary `0001 -> 0001,0002` upgrades while detecting altered history and incomplete artifacts. The read-only schema verifier then checks required tables, columns/types/nullability, constraints, and indexes including `source_revision` and the owner creation-order index. Only after verification is history published to the application graph.

### Mechanism 10: Role-Specific Liveness/Readiness and Deployment Composition

| Design obligation | Decision |
|---|---|
| Current root cause | Legacy `/health` is always HTTP 200, image health is API-only, worker has no role probe, and compose history users lack PostgreSQL settings/dependency. |
| Affected components/interfaces | Readiness aggregator, additive API health routes, one-shot worker probe, Dockerfile/compose healthchecks, API/worker/PostgreSQL environment/dependencies. |
| Invariants | Liveness never claims dependency readiness; readiness is derived from the same role snapshot/composition; a required unknown/degraded capability makes readiness fail; worker never calls API health. |
| Failure and cleanup | Probe calls have finite timeouts and sanitized output. A failed readiness check does not kill a live process directly; orchestration can stop routing/restart according to policy. Probe resources close in the same invocation. |
| Public-contract impact | Existing `/health` exact keys/status remain. Additive `/health/live` and `/health/ready` provide role semantics; worker uses a CLI/local role probe rather than HTTP. |
| Ordering/concurrency | Readiness snapshot is atomically read; stale background heartbeat/evidence becomes unready after a finite age. PostgreSQL readiness requires verified schema, not only TCP success. |
| Deterministic tests | Capability-state truth table, exact legacy health snapshot, API endpoint status matrix, atomic worker parent/child evidence files with fake clocks/PIDs, stale/generation mismatch, probe modes/timeouts, static compose/Docker checks, and role/package command checks. |

API endpoints:

- `GET /health` remains the exact preserved Milestone 4 payload and HTTP 200.
- `GET /health/live` returns 200 when the API process/event loop can respond; it does not inspect Redis/PostgreSQL/provider readiness.
- `GET /health/ready` returns 200 only when all capabilities required by `StartupSnapshot` are ready and 503 with sanitized component categories otherwise.

Worker health has an explicit local observation channel. The Celery parent owns a mode-0700 health directory and atomically publishes a sanitized JSON aggregate by writing a mode-0600 temporary file and `os.replace`-ing it to a mode-0600 final file in that directory. The record contains a schema/generation, parent PID/start identity, last parent heartbeat, common snapshot/topology decision, broker evidence age, configured-history verification/readiness, expected child count, and per-child readiness summaries—never URLs, settings reprs, watch IDs, or reservation data. Each prefork child atomically writes/removes its own mode-0600 generation-bound state file after common resource initialization/shutdown; the parent aggregator accepts only live child PIDs with the current generation and periodically refreshes the aggregate. Stale files from another PID/start identity are ignored and bounded startup/shutdown cleanup removes them.

The finite one-shot worker probe has distinct `--mode live` and `--mode ready` paths. Liveness reads the aggregate, verifies the recorded parent PID/start identity and heartbeat age, and does not contact API/dependencies. Readiness additionally requires a non-stale aggregate, at least the configured minimum ready child count, `SUPPORTED_PRIMARY`, current broker evidence, and verified configured history with no required degraded backlog. A malformed, missing, stale, or generation-mismatched record fails closed with sanitized output. The probe does not start a worker, resource loop, or network client and never calls `127.0.0.1:8000`; fake files/clocks/PIDs and parent-child aggregation tests prove the contract. A POSIX permission test running as the owning non-root identity verifies parent publication, child publication/removal, and both probe modes through the mode-0700 directory and mode-0600 files.

The image may retain an API default healthcheck, but compose overrides it per role. The `api` and `worker` services build the same package/image, receive the same `REDIS_URL`, `POSTGRES_URL`, and relevant immutable settings, and depend on healthy Redis and PostgreSQL under the app profile when durable history is configured. The worker command remains Celery. PostgreSQL's existing image/port/volume/healthcheck and default infra-only compose behavior are preserved.

### Mechanism 11: Deterministic Exploration Gates for Unproven Hypotheses

| Design obligation | Decision |
|---|---|
| Current root cause | Plausible admission, contention, and notifier risks have no reproducible trace or approved behavior. |
| Affected components/interfaces | Test-only deterministic harnesses first; production admission/cancel/delivery interfaces only after a gate is approved. |
| Invariants | Exploration cannot silently modify auth, 429 behavior, cancellation status, retry classes, or delivery guarantee; every decision records seed/trace/observations. |
| Failure and cleanup | Harnesses use finite budgets, fake providers/repos/notifiers, reset globals/caches, and deny sockets. A non-reproduced hypothesis is documented as not proven. |
| Public-contract impact | None until a follow-up approved requirement selects behavior. Existing no-auth and retry contracts remain. |
| Ordering/concurrency | Barriers control exact admission bursts, five-round CAS fences, notifier side-effect points, and role startup order. |
| Deterministic tests | The three gates below plus API/worker role-composition parity; each produces a minimal trace and classification. |

1. **Admission-control gate**: drive bounded concurrent bursts through parse/provider/watch endpoints using fake paid-call counters, queue capacity, and fake clocks. Record maximum admitted concurrency/work, latency-independent resource budget, and current response behavior. Only a reproducible safety/cost counterexample plus approved response semantics can authorize authentication, rate limiting, or 429 changes.
2. **CAS-contention gate**: force the cancellation script to fence each of the current bounded attempts while authoritative state remains observable. After the bound, perform a controlled final read and classify missing, terminal, active-contention, or corrupt outcomes. A new retryable/409/503 result or changed retry bound requires a reproduced active-contention trace and approved public mapping; 404 remains reserved for truly missing state.
3. **Notifier-guarantee gate**: inject failure before side effect, after side effect before acknowledgement, lost acknowledgement, lease expiry, and redelivery with/without idempotency-key support. Classify definite retry versus uncertainty and approve at-most-once, idempotent-at-least-once, or manual uncertain recovery per transport. Generic throws remain `UNCERTAIN`; they do not silently select a guarantee.

### Specific Changes Required

| File/component | Design change during a later implementation phase |
|---|---|
| `backend/application/*` or focused `backend/services/*` modules | Define application ports, committed facts, projection pump/reconciler, owner query, terminal effects, lifecycle ledger, and shared policy values. |
| `backend/api/contracts.py`, dependencies, routes, `main.py` | Add public page/error DTOs, snapshot-only dependencies, use-case wiring, temporal parity, and additive liveness/readiness. |
| `backend/config.py` | Parse from an injected immutable environment, preserve explicit/omitted origin, bound new capacities/tolerances, hide secrets, and produce safe endpoints. |
| `backend/models/reservation.py`, `watch.py`, `watch_runtime.py` | Add complete state/UTC/order/result validators, runtime v3 migration, private owner, and validated update helpers. |
| `backend/data/venues.py`, mock adapter | Separate inherit/closed semantics and align generated/rejected calendar support with accepted horizon. |
| `backend/db/repositories/watches.py`, decisions, scripts | Return committed facts, maintain owner/scan/event indexes, dual-write and resumably backfill legacy indexes, classify legacy events conservatively, implement delivery claims/pages and authority-token fencing, bounded cleanup, and preserve all Milestone 3 atomic decisions. |
| `backend/db/repositories/mock_booking.py`, scripts | Add bounded pin reconciliation and per-class cleanup/backlog accounting. |
| `backend/db/repositories/watch_history.py` | Implement revision-guarded writes and created-at/watch-id keyset pages behind application ports. |
| `backend/db/postgres.py`, migrations, `pyproject.toml` | Package resources, migration set/checksums, schema fingerprint, safe errors, and verified pool publication. |
| `backend/services/watch_service.py`, `watch_recovery.py`, `notification_service.py`, `readiness.py` | Consume committed effects, unify recovery expiry, remove awaited optional I/O, use privacy-safe delivery, and record bounded/loop evidence. |
| `backend/workers/celery_app.py`, `backend/workers/tasks/monitor_watch.py`, worker bootstrap/health | Capture one worker snapshot, gate topology before consumer start, compose common history/effects, publish atomic parent/child health evidence, retain exact retries/results/runner lock, and ledger-close every initialized resource. |
| `Dockerfile`, `infra/docker-compose.yml`, one-shot health module | Override role checks, wire PostgreSQL to API/worker, and retain existing infrastructure/profile behavior. |
| `tests/*` and contract artifacts | Add deterministic exploration/fix/preservation suites, generated traces, package/container checks, and socket denial. |

### Requirement Traceability

| Defect | Expected | Primary mechanism | Correctness property | Deterministic evidence |
|---|---|---|---|---|
| 1.1 | 2.1 | Common API/worker projection factory | 3 | Worker composition parity and unchanged result/retry matrix |
| 1.2 | 2.2 | Finite non-awaiting coalescing publisher | 3 | Blocked writer and generated capacity bursts |
| 1.3 | 2.3 | Private owner in runtime v3/committed facts | 4 | Fail-first projection then later transition |
| 1.4 | 2.4 | Source revision and conditional upsert | 4 | Revision permutation/duplicate oracle |
| 1.5 | 2.5 | Bounded live/history merge with live precedence | 4 | Generated overlap and cleanup datasets |
| 1.6 | 2.6 | Three-state client-ID parser | 4 | Generated omitted/valid/malformed partition |
| 1.7 | 2.7 | Explicit unavailable reader/error and readiness | 4 | Disabled/failed/empty/recovered matrix |
| 1.8 | 2.8 | Immutable-key cursor pages | 4 | Multi-page traversal over >100 records |
| 1.9 | 2.9 | Application use cases and public DTO/OpenAPI | 4 | Dependency override and contract drift checks |
| 1.10 | 2.10 | Explicit capability state and staged startup | 5 | Omitted/valid/invalid/unreachable matrix |
| 1.11 | 2.11 | Complete browser-origin grammar | 5 | Generated URL component classification |
| 1.12 | 2.12 | Lifecycle ledger plus migration/schema verifier | 5 | Failure-at-each-phase close-count tests |
| 1.13 | 2.13 | Safe endpoint/control errors | 5 | DSN sentinel search across all surfaces |
| 1.14 | 2.14 | Deterministic tasks 1–7 boundary suite | 5 | Worker/HTTP/fake pool/OpenAPI/package tests |
| 1.15 | 2.15 | Shared committed-facts terminal effects | 6 | Recovery vs normal expiry and repeated recovery |
| 1.16 | 2.16 | Immutable StartupSnapshot | 7 | Environment mutation and retained-error identity |
| 1.17 | 2.17 | Transactional composition/LifecycleLedger | 7 | Acquisition/close failure permutations |
| 1.18 | 2.18 | Common fail-closed TopologyDecision | 8 | API/worker supported/unknown/unsupported matrix |
| 1.19 | 2.19 | Independent worker resource ownership | 7, 8 | Every cache subset and repeated signals |
| 1.20 | 2.20 | Secret wrapper and controlled diagnostics | 9 | Generated Redis/Postgres sentinel URLs |
| 1.21 | 2.21 | Shared second-precise ReservationPolicy | 10 | Direct/orchestrator fake-clock parity |
| 1.22 | 2.22 | Full validators and validated evolvers | 10 | Constructor/JSON/update generated invalid states |
| 1.23 | 2.23 | Explicit closure and aligned calendar support | 10 | Sunday sentinel and support-edge cases |
| 1.24 | 2.24 | Privacy-safe event observability | 11 | Rendered log structured sentinel tests |
| 1.25 | 2.25 | Durable delivery state and explicit uncertainty | 11 | Failure-point/redelivery state-machine traces |
| 1.26 | 2.26 | Stable bounded page/bulk protocols and resumable legacy-index epoch | 12 | Large-index call/memory budgets plus dual-write/backfill instrumentation |
| 1.27 | 2.27 | Token-fenced AuthorityGuard and deadline-driven wakes | 12 | Paused-operation lease/horizon/loop-failure fake-clock traces |
| 1.28 | 2.28 | Per-class bounded cleanup report | 12 | Repeated drain/idempotence/backlog oracle |
| 1.29 | 2.29 | MigrationSet/checksum/schema/package identity | 13 | Set mutation, valid prefix upgrade, and built artifact tests |
| 1.30 | 2.30 | Role health evidence/healthchecks and Postgres composition | 14 | Parent-child probe files plus static compose/Docker checks |
| 1.31 | 2.31 | Dedicated semantic readiness | 14 | Capability truth table and status matrix |
| 1.32 | 2.32 | Complete deterministic evidence plan | 15 | Network-denied full focused suite |
| 1.33 | 2.33 | Admission/CAS/notifier exploration gates | 16 | Minimal deterministic traces before policy change |

| Preservation | Preserved artifact | Correctness property | Regression evidence |
|---|---|---|---|
| 3.1 | Headerless unowned creation | 17 | Existing watch API/service baseline |
| 3.2 | All parse-and-book statuses/fields | 17 | Finite result contract matrix |
| 3.3 | Exact public Watch/enums | 17 | JSON/OpenAPI schema snapshot |
| 3.4 | Unscoped and by-ID route behavior | 17 | Existing route tests and streamed-array equivalence |
| 3.5 | Live repository/single-flight authority | 18 | Repository state-machine oracle |
| 3.6 | Live success after projection failure | 18 | Differential no-history/failing-history tests |
| 3.7 | Unconfigured standalone mode | 19 | Cleared-environment API startup |
| 3.8 | CORS/non-browser semantics | 19 | Existing CORS matrix |
| 3.9 | Owner isolation/history-only terminal state | 19 | Generated multi-owner cleanup data |
| 3.10 | Exact legacy `/health` contract | 19 | Exact-key/status/vocabulary snapshot |
| 3.11 | Worker result/retry/runner/cleanup | 20 | Sibling worker suite plus additive resources |
| 3.12 | Independent HTTP-only frontend | 21 | Static import and generated-client checks |
| 3.13 | Existing deterministic tests | 21 | Unweakened full suite with socket denial |
| 3.14 | Startup logging/error and worker retry ownership | 20 | Imported sibling preservation matrices |
| 3.15 | Milestone 3 production guarantees | 18 | Existing policy/recovery/retention suites |
| 3.16 | Valid temporal/model values | 22 | Differential generated valid values |
| 3.17 | Supported venue/mock behavior | 22 | Existing venue/mock booking oracle |
| 3.18 | In-memory/Redis equivalence | 18 | Shared generated operation traces |
| 3.19 | Successful delivery deduplication | 23 | Same-event repeated redelivery |
| 3.20 | No auth; opaque scope only | 17 | Endpoint contract matrix and gate assertion |
| 3.21 | Provider safety/deterministic doubles | 22 | Existing provider normalization and network denial |

## Testing Strategy

### Validation Approach

Validation follows the bugfix two-phase discipline. First, deterministic exploration checks run against the unfixed behavior to reproduce each confirmed counterexample and classify the three unproven hypotheses. Refuted root-cause assumptions require design revision before implementation. Second, the same scenarios verify the fixed properties, followed by imported sibling/Milestone preservation suites and complete deterministic validation. No test starts a development server, worker, watcher, browser, live Redis/PostgreSQL/Celery broker, OpenAI call, or provider call.

### Exploratory Bug Condition Checking

**Goal**: Surface concrete counterexamples before implementation and distinguish confirmed defects from Requirement 1.33 hypotheses.

**Test Plan**: Use fake pools/connections, exact-script fakeredis where already pinned, fake clocks/monotonic clocks, barriers/events, finite in-memory queues, direct task `.run`, FastAPI `TestClient`, static package/container parsing, and network denial.

**Confirmed-scenario checks:**

1. Compare current API and worker service graphs and show worker projection is absent.
2. Block the current history recorder and show create/dispatch/poll/cancel latency waits.
3. Fail the first owner write, reorder writes, and compare stale history with newer live state.
4. Exercise malformed/omitted IDs, disabled/empty/failing history, update-vs-create order, and >100 records.
5. Inject every configured PostgreSQL/CORS/migration/pool failure and search sentinel secrets.
6. Expire through recovery and show no current projection/notification call.
7. Mutate environment after startup and show the orchestrator dependency can reparse it.
8. Fail after each resource acquisition and during each close; observe leak/skip behavior.
9. Throw from topology inspection in API and build the worker; observe fail-open/omitted gate.
10. Initialize worker Redis without runner and invoke cleanup; observe the open client.
11. Exercise reversed windows, horizon/same-minute boundaries, invalid model JSON/copies, closed Sunday, and calendar terminal year.
12. Capture default notification logs and post-commit notifier failures/redelivery.
13. Instrument whole-index recovery/listing, expired pins, lease passage, horizon entry, and loop failure.
14. Seed event/tombstone/pin/booking cleanup classes and inspect missing counts/backlog.
15. Discover empty/duplicate/changed migration sets and inspect wheel/container resources.
16. Evaluate legacy vs dedicated role health and API/worker/PostgreSQL compose wiring.

**Exploration-only gates:** run the bounded admission burst, forced five-round cancellation contention, and notifier failure-point/redelivery harnesses from Mechanism 11. Record a minimal trace and classification. If no counterexample reproduces, retain current behavior and mark the hypothesis unproven; do not weaken the gate.

### Fix Checking

**Goal**: Verify every input satisfying the bug condition meets its paired expected behavior.

```text
FOR ALL input WHERE isBugCondition(input) DO
  result := CompleteBackendFixed(input)
  ASSERT expectedBehavior(input, result)
END FOR
```

Each confirmed exploration test is retained and inverted to the corrected assertion. Generated tests report the minimal seed/trace, revision ordering, page cursor, failure phase, lease time, or delivery-state sequence.

### Preservation Checking

**Goal**: Verify all non-buggy inputs and all sibling-owned behavior remain equivalent.

```text
FOR ALL input WHERE NOT isBugCondition(input) DO
  original := observable(CompleteBackend(input))
  fixed := observable(CompleteBackendFixed(input))
  ASSERT fixed = original
END FOR
```

Existing tests are not weakened. Differential harnesses compare public HTTP, live repository decisions, queue publications, worker retry calls/results, mock booking outcomes, successful notification counts, legacy `/health`, and supported deployment artifacts. Sibling suites are treated as imported contracts, not rewritten expectations.

### Deterministic Test Infrastructure

- Fake wall and monotonic clocks control UTC, local/DST, lease, horizon, retry, retention, and readiness age.
- `asyncio.Event`, thread barriers, and scripted decisions control interleavings without sleep-based races.
- Instrumented page repositories count reads, batch size, retained objects, pipelines, and continuation calls.
- Fake pool/connection/migration/package resources model every failure point and close count.
- Exact production Redis scripts run in the already pinned in-process Lua-capable fake where script semantics matter.
- Finite model-based traces compare in-memory and Redis decisions with a sequential oracle.
- Notification doubles expose pre-side-effect, post-side-effect, lost-acknowledgement, and idempotent replay points.
- Captured logs render messages and structured extras; secret/privacy sentinel sets are searched exactly.
- A socket-denial fixture fails any accidental network access immediately.
- Generated cases use fixed seeds/derandomized finite domains and bounded example counts; no new PBT dependency is required.

### Unit Tests

- Client-ID classification, public errors, cursor codec/integrity, DTO/OpenAPI schemas, and dependency-before-side-effect ordering.
- Projection coalescing/capacity/close/drain and SQL revision/owner rules.
- Runtime v2-to-v3 migration, unknown version rejection, UTC/order/state/result validators, and validated update helpers.
- Terminal delivery transitions, claim fencing, retry/uncertain/exhausted decisions, privacy-safe logging, and cleanup eligibility.
- Immutable snapshot value/error states and lifecycle rollback/shutdown permutations.
- Redis topology classification and safe endpoint/redaction formatting.
- Shared temporal decisions, second-precise slot boundaries, reversed windows, horizon/calendar support, and explicit weekly closure.
- Stable repository pages, authority renewal margins, wake deadline calculation, cleanup class reports, and readiness transitions.
- Migration entry parsing, checksum/set identity, applied-prefix comparison, schema fingerprint, and package identity.
- API/worker liveness/readiness truth tables and finite probe timeout behavior.

### Property-Based Tests

- Generate envelope revisions, duplicates, and completion permutations; persisted history equals the maximum source revision.
- Generate owner-partitioned live/history sets, overlaps, pages, insertions newer than the cursor, and cleanup; traversal is ordered, unique, complete, isolated, and live-wins.
- Generate projection bursts against blocked consumers; occupancy never exceeds capacity and live observations equal the no-history baseline.
- Generate acquisition/close failure traces; every acquired resource closes exactly once and the primary failure remains authoritative.
- Generate connection URLs and failure surfaces; no secret sentinel appears in any observable string.
- Generate valid/invalid temporal/model/runtime/result states across construction, JSON reconstruction, and update; all paths agree.
- Generate repository sizes/page mutations, lease durations, and cleanup classes; per-pass budgets hold and repeated passes drain reachable due work.
- Generate migration sets/applied histories; only the required ordered checksummed prefix and schema/package identity succeeds.
- Generate terminal delivery/redelivery traces; successful idempotent events deliver once and uncertain non-idempotent outcomes never auto-select a guarantee.
- Generate valid operation traces against in-memory and Redis implementations; preserved decisions remain equivalent.

### Integration Tests

- FastAPI `TestClient` covers headerless/valid/malformed creation, direct temporal errors, all parse-and-book statuses, owner page success/error/pagination, unchanged unscoped/by-ID routes, CORS, exact legacy `/health`, and additive live/ready endpoints.
- API composition tests prove immutable snapshot use, common topology policy, configured fail-fast history, supported standalone mode, atomic graph publication, rollback, and isolated shutdown.
- Worker composition tests prove topology/history/effect parity, exact task result/retry behavior, runner serialization, every initialization subset, and repeated cleanup signals.
- Live/history/effects integration uses authoritative in-memory/exact-script repositories plus fake PostgreSQL/notifiers to prove owner recovery, monotonic projection, live reconciliation, recovery expiry, delivery recovery, and terminal cleanup.
- Recovery integration proves bounded pages/bulk reads, mid-pass renewal/loss, horizon wake tolerance, loop-failure readiness, and per-class backlog accounting.
- Packaging tests build or inspect the wheel/container context without network access and assert migration resources, checksums, schema/package version, and role probe modules are present.
- Static compose/Docker tests preserve Redis/PostgreSQL definitions and app profile while asserting PostgreSQL wiring and role-specific healthchecks.
- Contract-drift tests generate OpenAPI deterministically and compile/check the future frontend client contract without backend imports.
- One-shot validation uses focused `python -m pytest` invocations, `python -m mypy backend`, `python -m compileall backend tests`, and `git diff --check`; no long-running command is started.
