# Milestone 3 Production Path Hardening Bugfix Design

## Overview

This bugfix replaces the current snapshot-based watch loop with a bounded, deadline-aware, repository-coordinated state machine while preserving the existing public watch statuses, poll outcomes, immediate first check, API request/response JSON, and worker retry/resource contracts.

The current implementation reads a `Watch`, calls the provider, writes a whole replacement document, and then separately enqueues a successor. That sequence is safe only in one process with one delivery. The fixed path gives every scheduled cadence a stable `window_id`, atomically grants one expiring claim, fences commits by token and revision, and records the next schedule marker in the same repository transaction as the state change. Redis uses explicit Lua scripts for conditional multi-key changes; it does not rely on an unguarded pipeline or assume that `MULTI` alone provides compare-and-set behavior. The in-memory repository implements the same decisions under one `asyncio.Lock`.

Availability attempts become a user budget distinct from provider-outage retries. At creation, the service derives the number of availability checks needed from the remaining absolute lifetime and the earliest normal jitter delay. It then applies a finite configurable safety ceiling. The default ceiling is large enough for default same-day, +1-day, +7-day, and +30-day watches, including DST-length days. If an operator intentionally configures a shorter ceiling, the direct watch response and PromptRouter message state that limitation rather than promising unqualified monitoring through the date.

`MockBookingAdapter` becomes stateless apart from configuration and receives a `MockBookingStateRepository`. API and worker adapters share Redis state in distributed mode; services in one fallback process share one in-memory repository. Slot publication, booked tombstones, confirmations, and idempotency records are atomic and capacity-bounded.

A startup recovery coordinator scans the selected repository after final store/queue wiring. Redis replicas coordinate with a finite leader lease and per-window dispatch leases; asyncio mode reconciles only records visible to its selected repository and makes no durability claim for process-local memory. Recovery classifies missing, corrupt, terminal, future, due, expired, and exhausted records and uses the same schedule-marker protocol as normal polling.

Delivery is phased but scope is not dropped:

1. Characterize `monitor_watch`, `celery_app`, `main._attach_redis`, current watch behavior, and the `watch-route-worker-retry-hardening` contracts before production edits.
2. Add policy calculation, runtime metadata, atomic repository operations, single-flight polling, and durable schedule markers.
3. Add shared mock state, booking/idempotency atomicity, and bounded cleanup.
4. Add recovery, outage backoff, terminal retention, health readiness, and container assets.
5. Run focused model/property tests, integration tests with fakes, the complete existing suite, and optional container configuration smoke checks.

No live Redis server, Celery broker/worker, provider, development server, or watcher is required by the automated tests.

## Glossary

- **Bug_Condition (C)**: Any supported watch execution in which lifetime policy, delivery concurrency, process-local mock state, restart recovery, outage pacing, retention, health, or deployment behavior violates Requirements 2.1–2.15.
- **Property (P)**: The corrected observable behavior for inputs satisfying `C`, including deadline-capable defaults, fenced single-flight transitions, recoverable scheduling, shared atomic mock state, and truthful operability reporting.
- **Preservation**: Equivalence of the fixed and original behavior outside `C`, especially the exact public statuses/outcomes, immediate first check, normal jitter intent, date expiry, optional-infrastructure fallback, stable booking key, worker retry classification, cleanup, and task result shape.
- **Availability attempt**: One cadence window whose provider work completes without `AdapterError` and whose fenced result is committed. It is stored in the existing public `Watch.attempts` field.
- **Safety ceiling**: The finite configured upper bound on availability attempts, stored as the public `Watch.max_attempts` after lifetime derivation.
- **Required allowance**: `1 + ceil(remaining_lifetime / earliest_normal_delay)`, including the immediate first check.
- **Deadline-capable policy**: A policy whose safety ceiling is at least the required allowance at creation.
- **Cadence window**: One logical scheduled opportunity to check or expire a watch. It has one immutable `window_id` even if the queue delivers it repeatedly.
- **WatchRuntime**: Internal repository metadata stored separately from the public `Watch` JSON: schema version, revision, cadence sequence/window, policy facts, outage counter, and terminal cleanup time.
- **Fencing token**: A monotonically increasing claim generation issued atomically by the repository. A former owner cannot commit with an older token.
- **Claim lease**: Expiring ownership of one cadence window. Its default is 120 seconds, greater than Celery's existing 90-second hard task limit.
- **Schedule marker**: The repository-authoritative record of a logical current/future window. It is written atomically with watch state and survives a crash between commit and broker enqueue.
- **Dispatch lease**: A short per-window lease that prevents replicas from repeatedly enqueueing the same schedule marker while still permitting redispatch after a lost broker message.
- **Logical successor**: The one persisted next cadence window. Duplicate physical broker messages are possible in an at-least-once system, but all refer to that same logical successor and only one can poll.
- **Terminal event**: The unique `AVAILABILITY_FOUND`, `BOOKED`, or `EXPIRED` event created with a terminal transition. Cancellation remains observable through watch state and does not gain a new notification side effect.
- **MockBookingStateRepository**: Injectable in-memory/Redis boundary for generated slots, operation pins, bookings, booked-slot tombstones, and idempotency records.
- **Recovery coordinator**: Startup and bounded follow-up reconciliation that repairs indexes, expires ineligible watches, and dispatches persisted schedule markers.
- **Original function (F)**: The current snapshot-save `WatchService.poll_once`, process-local `MockBookingAdapter`, queue, repository, startup, and health behavior.
- **Fixed function (F')**: The coordinated implementation described here.

## Bug Details

### Bug Condition

The defect is present whenever a valid watch can terminate materially earlier than its advertised deadline under defaults, concurrent deliveries can perform the same logical work, stale work can overwrite a newer state, mock provider state differs by process, persisted active work is not recoverable, provider outages consume the user budget or hot-poll, terminal data/indexes grow without bound, readiness claims exceed actual checks, or the service cannot be run unattended from repository assets.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type ProductionPathObservation
  OUTPUT: boolean

  lifetimeBug := input.defaultPolicy
                 AND input.reservationOffset IN [SAME_DAY, PLUS_1, PLUS_7, PLUS_30]
                 AND input.attemptExhaustionTime < input.expiresAt

  messagingBug := input.policySupportsDeadline = FALSE
                  AND input.responseClaimsUnqualifiedMonitoringUntilDate

  concurrencyBug := input.sameWindowDeliveryCount > 1
                    AND input.noPriorOwnerCrashOrLeaseExpiry
                    AND (input.providerCallCount > 1
                         OR input.logicalSuccessorCount > 1
                         OR input.committedAttemptCount > 1
                         OR input.terminalEventCount > 1)

  fencingBug := input.staleOwnerCommitted
                OR input.terminalStateWasOverwritten
                OR input.takeoverOccurredBeforeLeaseExpiry
                OR input.takeoverImpossibleAfterLeaseExpiry

  mockStateBug := input.adapterProcessCount > 1
                  AND (input.sameSlotHasMultipleConfirmations
                       OR input.idempotentReplayDiffers
                       OR input.bookedSlotAppearsAvailableDuringRetention
                       OR input.unbookedSlotStateExceedsCapacity)

  recoveryBug := input.persistedActiveWatch
                 AND (input.hasNoRecoverableSchedule
                      OR input.recoveryScheduledAfterExpiry
                      OR input.recoveryScheduledSameWindowMoreThanOnceLogically
                      OR input.staleIndexMemberWasReturnedAsActive)

  outageBug := input.providerRaisedAdapterError
               AND (input.availabilityAttemptsIncreased
                    OR input.delayViolatesCappedExponentialPolicy
                    OR input.providerCalledWithLessThanDelayFloorRemaining)

  retentionBug := input.terminalRetentionElapsed
                  AND (input.documentStillRetainedWithoutBound
                       OR input.allIndexMembershipStillObservable)

  readinessBug := input.healthQueueMode != input.selectedQueueMode
                  OR input.readiness = READY AND input.requiredProbeWasNotPerformed

  deploymentBug := NOT input.repositoryProvidesUnattendedApiAndWorkerBuildPath

  RETURN lifetimeBug OR messagingBug OR concurrencyBug OR fencingBug
         OR mockStateBug OR recoveryBug OR outageBug OR retentionBug
         OR readinessBug OR deploymentBug
END FUNCTION
```

### Examples

- At the default 180 ± 30 second schedule, a +30-day watch may need roughly 17,857 availability checks under the earliest 150-second sequence. The current fixed value of 200 can expire it in about ten hours. The fixed default derives the allowance and retains eligibility until calendar expiry.
- If an operator sets a safety ceiling of 100, the watch keeps `max_attempts=100`; PromptRouter says it can stop after 100 availability checks, and the direct create response carries the same limitation in documented response headers.
- Four redeliveries for `window_id=watch_1:7` race. One receives a claim; three return a no-op without provider calls, successor dispatches, bookings, attempt changes, or notifications.
- A worker claims revision 8/token 21, calls the provider, and is paused. A cancellation commits revision 9 and `CANCELLED`. The worker's conditional commit fails and cannot restore `ACTIVE`.
- A worker dies after a search but before commit. After the 120-second lease expires, recovery reuses the same window identity. At most one result for that identity is committed; an auto-book replay uses `watch:{watch_id}` and returns the original protected confirmation.
- API process A publishes and books a mock slot. Worker process B, using another adapter over the same Redis state repository, replays the same key to the same confirmation and excludes the slot from all searches until protected retention ends.
- A process commits a normal miss and the next schedule marker, then dies before `apply_async`. Startup/periodic reconciliation sees that marker and dispatches it; no watch is wedged by the commit/enqueue crash window.
- Two API replicas start together. Only the Redis recovery-leader lease holder scans, while per-window dispatch leases still make an accidental overlap harmless. If the leader dies, its lease expires and another replica resumes.
- Three consecutive search `AdapterError` results schedule approximately 180, 360, and 720 seconds before jitter, capped at 3,600 seconds, without increasing `attempts`. A successful empty search resets the counter, and the next miss returns to 180 ± 30 seconds.
- A terminal watch remains retrievable for seven days, is never returned from the active index, and is removed with its all-index membership by due cleanup; reads also prune missing/corrupt stale members.
- `/health` still returns HTTP 200 and top-level `status: ok`, but adds the selected `watch_queue`. Celery import alone yields `unknown`, not `ready`; a failed broker probe yields `degraded`.
- A same-day watch across a DST transition derives expiry from midnight after the reservation date in the configured zone, then uses UTC instants for remaining-lifetime arithmetic. It neither assumes every local day is 86,400 seconds nor schedules a provider call at/after expiry.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- `POST /api/watches` retains its request body, query parameters, status 201, and public `Watch` JSON; creation persists an `ACTIVE` watch and records an immediate first schedule.
- `DELETE /api/watches/{id}` still changes only an active watch to `CANCELLED`, clears `next_check_at`, and makes queued work stop.
- Public statuses remain exactly `ACTIVE`, `FOUND`, `BOOKED`, `EXPIRED`, and `CANCELLED`.
- Poll outcomes remain exactly `NO_AVAILABILITY`, `FOUND`, `BOOKED`, `EXPIRED`, `ALREADY_FINISHED`, and `UNKNOWN_WATCH`; attempt exhaustion still maps to `EXPIRED`.
- A healthy empty provider result schedules one normal successor with the existing jitter intent, including 150–210 seconds for the default 180 ± 30 schedule.
- The first check remains immediate and is not jittered.
- `expires_at` remains midnight after the reservation date in `RESERVATION_TIMEZONE`, converted to UTC, with no work scheduled beyond that instant.
- Missing/invalid watch settings still produce the retained `ConfigurationError` as HTTP 503 before date validation or watch creation.
- Redis/Celery remain optional. Missing/unreachable Redis uses the shared process-local state plus asyncio queue; missing Celery can use Redis persistence plus asyncio when that is the actually selected path.
- Auto-book keeps the exact idempotency key `watch:{watch_id}`.
- Mock slot identities, capacities, availability generation, and explicit `MOCK_CONFIRMED` behavior remain deterministic and never imply a real-provider guarantee.
- `/health` preserves HTTP success and the existing `status`, `service`, `config`, and actual `watch_store` meanings.
- `monitor_watch` continues to retry only recognized Redis connection/timeout and broker operational failures, with the original exception, 60-second countdown, and maximum three retries. Programming, validation, persisted-state, and other non-transient exceptions still propagate immediately.
- The persistent `asyncio.Runner` remains serialized and worker queue/Redis/runner cleanup remains lazy and idempotent.
- A successful Celery task still returns exactly `watch_id`, string `outcome`, and `retry_in_seconds` with existing meanings.
- Existing Redis/PostgreSQL compose data and health behavior and Windows-friendly host commands remain available.

**Scope:**
Inputs outside the bug condition remain behaviorally equivalent. Additive operational metadata is limited to documented creation response headers and additive `/health` fields; public watch JSON and poll enums do not change. Internal task arguments, repository sidecars, Redis keys, and queue bookkeeping are not public API.

## Hypothesized Root Cause

Based on the current repository contracts and pinned dependencies, the likely causes are:

1. **A static attempt count models the wrong quantity**: `WatchService.create` copies `WATCH_MAX_POLL_ATTEMPTS` directly to every watch. It never considers `expires_at` or `PollSchedule.earliest_delay`, and `PromptRouter` always emits the deadline promise.
   - `WatchSettings` bounds interval but currently gives attempts no upper bound.
   - `Watch.is_exhausted` treats attempts and calendar time as equivalent terminal causes.

2. **Whole-document replacement has no compare-and-set boundary**: `WatchRepository.save` replaces snapshots. Redis uses a pipeline for grouping writes but has no watched revision, lease, or fencing condition.
   - Duplicate tasks carry only `watch_id`, so they have no common cadence identity.
   - Cancellation, fulfillment, expiry, and rescheduling all save independently from the snapshot read earlier.

3. **State commit and enqueue are separate failure domains**: `_reschedule` saves `next_check_at` and then calls the queue. A crash or dispatch error between them leaves no durable, independently dispatchable outbox marker.

4. **Provider state is owned by adapter instances**: `MockBookingAdapter` stores slots, booked IDs, and idempotency confirmations in three dictionaries guarded by a lock local to one adapter. Search writes `_slots` outside that lock.
   - `main.py` and the Celery worker construct different adapter instances.
   - Generated slots have neither capacity nor idle eviction.

5. **Startup selects infrastructure but does not reconcile work**: `_attach_redis` upgrades repository/queue wiring and exits. It never scans `ACTIVE_INDEX_KEY`, coordinates replicas, or recovers persisted `next_check_at` values.

6. **Adapter errors are flattened into ordinary misses**: `_search` returns `([], error)`, after which `poll_once` increments `attempts` and uses normal cadence. No consecutive-outage state or capped exponential schedule exists.

7. **Redis retention is index-only and permanent**: active membership is removed on terminal save, but terminal documents/all-index entries have no expiry schedule. `_load_many` skips bad records but does not prune their IDs.

8. **Health reports selection incompletely**: `watch_store` is accurate, but queue mode is omitted and no performed broker/recovery probe is represented.

9. **Worker/startup behavior lacks characterization coverage**: there are no current tests for `monitor_watch.py`, `celery_app.py`, or the Redis/Celery upgrade branches in `main.py`, making concurrency and retry changes risky without an observation-first stage.

10. **Deployment assets are incomplete**: compose defines Redis and PostgreSQL, while the repository has no actual root `Dockerfile` or `.dockerignore` despite a conceptual Dockerfile appearing in README structure text.

## Correctness Properties

The properties use the following predicate; each detailed property below is a projection of this single expected behavior rather than a duplicate definition.

```
FUNCTION expectedBehavior(input, result)
  INPUT: input of type ProductionPathInput
  INPUT: result of type ProductionPathObservation
  OUTPUT: boolean

  IF isBugCondition(input) THEN
    RETURN result.policyUsesCheckedLifetimeDerivation
           AND result.messageMatchesEffectivePolicy
           AND result.concurrentProviderCallsPerWindow <= 1
           AND result.providerCallsWithoutPriorCrashOrLeaseExpiryPerWindow <= 1
           AND result.attemptCommitsPerWindow <= 1
           AND result.logicalSuccessorsPerWindow <= 1
           AND result.terminalEventsPerTransition <= 1
           AND result.staleOwnersCannotCommit
           AND result.sharedMockStateIsAtomicAndBounded
           AND result.persistedSchedulesAreRecoverable
           AND result.outagesDoNotConsumeAvailabilityAttempts
           AND result.retentionAndIndexesAreBounded
           AND result.healthClaimsNoMoreThanWasProbed
           AND result.unattendedRolesAreDefined
  END IF

  RETURN result.publicBehaviorEqualsOriginalForNonBugInput
END FUNCTION
```

Property 1: Bug Condition - Deadline-Capable and Truthful Availability Policy

_For any_ valid watch lifetime and finite configured cadence, jitter, and safety ceiling, the fixed creation path SHALL derive the required allowance with checked integer ceil-division from the remaining UTC lifetime and earliest normal delay, SHALL use `min(required_allowance, safety_ceiling)` without unbounded work, SHALL preserve the immediate first check, and SHALL qualify every creation message/header whenever the ceiling is shorter than the required allowance.

**Validates: Requirements 2.1, 2.2, 2.3**

Property 2: Preservation - Existing Public Watch, Worker, and Local-Development Contracts

_For any_ input where the bug condition does not hold, the fixed system SHALL preserve immediate creation and public Watch JSON, cancellation, exact statuses/outcomes, healthy-miss jitter, timezone deadline behavior, optional infrastructure fallback, configuration-error precedence, stable watch booking keys, deterministic mock identity, top-level health semantics, explicit worker retry classification, serialized/lazy resource lifecycle, no-live-service tests, existing compose infrastructure/Windows flow, and the exact successful worker result shape.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13, 3.14, 3.15**

Property 3: Bug Condition - Single-Flight, Fenced, Recoverable Cadence Windows

_For any_ number or interleaving of Celery/asyncio deliveries, cancellation/terminal transitions, crashes, and lease takeovers for one `window_id`, the repository SHALL grant at most one unexpired owner, allow only that owner to call the provider during a lease epoch, commit at most one availability result/attempt and one logical successor or terminal event, reject every stale token/revision, preserve terminal monotonicity and booking idempotency, and permit takeover only after the finite lease expires. A post-crash owner MAY repeat provider work whose completion is unknowable, but it SHALL reuse the same window and still commit no more than once.

**Validates: Requirements 2.4, 2.5, 2.6**

Property 4: Bug Condition - Shared Atomic and Bounded Mock Booking State

_For any_ set of adapter instances sharing one selected state repository and any concurrent publication, search, booking, replay, pin, or cleanup operations, one slot SHALL have at most one protected confirmation across distinct keys, one key SHALL replay one confirmation across instances, booked slots SHALL remain unavailable during protection, and unbooked state SHALL remain within configured capacity/idle limits without evicting protected records.

**Validates: Requirements 2.7, 2.8**

Property 5: Bug Condition - Coordinated Startup and Schedule Recovery

_For any_ indexed persisted watch and any number of startup contenders or injected dispatch failures, recovery SHALL classify and prune missing/corrupt/terminal entries, atomically expire ineligible active watches, preserve future due times, dispatch due logical windows at most once per active dispatch lease, never schedule provider work after expiry, report failures, and allow a new coordinator after ownership expiry.

**Validates: Requirements 2.9, 2.10**

Property 6: Bug Condition - Characterization-First Isolated Validation

_For any_ implementation phase that changes production watch behavior, the relevant unfixed counterexample and preservation observations SHALL first be executable through deterministic fakes/doubles for both Redis/Celery and memory/asyncio contracts, and the resulting suite SHALL coexist with `watch-route-worker-retry-hardening` without a live service.

**Validates: Requirements 2.11**

Property 7: Bug Condition - Reproducible Unattended Container Roles

_For any_ repository container build and either supported role, the resulting image/compose configuration SHALL define a finite non-interactive API or worker command, SHALL provide an API health check, SHALL exclude local secrets/build noise, and SHALL preserve existing Redis/PostgreSQL service data and health definitions when application services are enabled.

**Validates: Requirements 2.12**

Property 8: Bug Condition - Terminal Retention and Index Self-Healing

_For any_ active-to-terminal transition and any missing, corrupt, or stale index member, the repository SHALL atomically remove active membership, retain the terminal document/list visibility for the configured window, remove due terminal data and all-index membership after that window, keep active documents durable, and prune invalid members from every read/cleanup/recovery path.

**Validates: Requirements 2.13**

Property 9: Bug Condition - Honest Queue and Recovery Health

_For any_ selected store/queue and any set of actually performed broker/queue/recovery probes, `/health` SHALL retain existing top-level semantics, report the actual `watch_queue`, and derive `ready`, `degraded`, or `unknown` only from those observations rather than imports or configured intent.

**Validates: Requirements 2.14**

Property 10: Bug Condition - Provider Outage Budget and Backoff

_For any_ sequence of search/auto-book `AdapterError` results, successful provider interactions, jitter draws, and remaining lifetimes, the fixed state machine SHALL consume no availability attempt for an outage window, apply checked `interval × 2^(n-1)` capped exponential jittered delay, reset the counter after success, honor the anti-hot-poll floor/deadline, and produce the exact `EXPIRED` terminal outcome at expiry.

**Validates: Requirements 2.15**

## Fix Implementation

### Architecture and Components

The fixed path keeps `WatchService` provider-neutral and adds explicit coordination boundaries:

1. **`AvailabilityPolicyFactory`** (`backend/services/watch_policy.py`): derives required/effective attempts and truthful policy text from a clock, `expires_at`, and `PollSchedule`.
2. **`WatchRepository` atomic protocol** (`backend/db/repositories/watches.py`): retains query/list compatibility and adds create, claim, commit, cancel, expire, dispatch, prune, and cleanup decisions. Redis and memory return the same typed decision enums.
3. **`WatchRuntime` internal model** (`backend/models/watch_runtime.py`): sidecar concurrency/policy metadata, never serialized in public Watch responses.
4. **`WatchService.poll_window(watch_id, window_id)`**: claims first, performs at most one provider interaction sequence, and conditionally commits. `poll_once(watch_id)` remains a compatibility wrapper that resolves the current window.
5. **`WatchScheduleDispatcher`** (`backend/workers/dispatcher.py`): dispatches repository schedule markers and records/release dispatch leases.
6. **`RecoveryCoordinator`** (`backend/services/watch_recovery.py`): leader-coordinated startup/index reconciliation with injected repository, dispatcher, clock, and owner ID.
7. **`MockBookingStateRepository`** (`backend/db/repositories/mock_booking.py`): atomic in-memory and Redis implementations injected into stateless mock adapters.
8. **`ReadinessTracker`** (`backend/services/readiness.py`): records selected modes and the last performed queue/recovery observations for `/health`.

`main.lifespan` builds fallback components first, performs the current optional Redis/Celery upgrade, then rebinds both `BookingService` and `WatchService` to adapter instances over the final shared mock state repository. Recovery runs only after final bindings exist. Shutdown order is recovery coordinator, queue, Redis client, and orchestrator; all closes are idempotent.

### Configuration and Availability Policy

Existing names remain accepted. `WATCH_MAX_POLL_ATTEMPTS` is documented as the availability-attempt safety ceiling rather than a universal fixed count.

| Setting | Default | Valid bound / relation |
| --- | ---: | --- |
| `WATCH_POLL_INTERVAL_SECONDS` | 180 | existing 15–3,600 |
| `WATCH_POLL_JITTER_SECONDS` | 30 | 0 ≤ jitter < interval |
| `WATCH_MAX_POLL_ATTEMPTS` | 25,000 | 1–1,000,000 |
| `WATCH_POLL_LEASE_SECONDS` | 120 | 91–3,600; strictly greater than the shared 90-second Celery hard limit |
| `WATCH_PROVIDER_CALL_TIMEOUT_SECONDS` | 55 | 1–59; below the existing 60-second soft limit |
| `WATCH_PROVIDER_BACKOFF_MAX_SECONDS` | 3,600 | at least the normal interval and at most 86,400 |
| `WATCH_TERMINAL_RETENTION_SECONDS` | 604,800 | 3,600–31,536,000 |
| `WATCH_RECOVERY_LEADER_LEASE_SECONDS` | 30 | 5–300 |
| `WATCH_RECOVERY_SWEEP_SECONDS` | 30 | 5–3,600 |
| `WATCH_DISPATCH_HORIZON_SECONDS` | 300 | 30–3,600; Celery receives no farther-future ETA |
| `MOCK_SLOT_CAPACITY` | 10,000 | 1–100,000 |
| `MOCK_SLOT_IDLE_TTL_SECONDS` | 3,600 | 60–604,800 |
| `MOCK_BOOKING_RETENTION_SECONDS` | 604,800 | 604,800–31,536,000 |

Parsing rejects overlong integer text before conversion, signs where not allowed, values outside bounds, and inconsistent combinations. It never loops or allocates based on a configured integer. Shared constants define Celery hard/soft limits so lease validation cannot silently diverge from `celery_app.conf`.

Policy derivation uses integer microseconds, not floating-point division:

```
FUNCTION deriveAvailabilityPolicy(now, expiresAt, schedule, safetyCeiling)
  ASSERT now and expiresAt are timezone-aware UTC instants
  earliestUs := checkedSecondsToMicroseconds(schedule.earliestDelay)
  remainingUs := MAX(0, checkedTimedeltaToMicroseconds(expiresAt - now))
  intervalCount := checkedCeilDivide(remainingUs, earliestUs)
  required := checkedAdd(intervalCount, 1)  // immediate check
  effective := MIN(required, safetyCeiling)

  RETURN Policy(
    requiredAttempts = required,
    effectiveAttempts = effective,
    supportsDeadline = (effective = required),
    limitingReason = IF effective = required THEN CALENDAR ELSE SAFETY_CEILING
  )
END FUNCTION
```

The new default 25,000 exceeds the worst default +30-day allowance (31 local calendar days plus a possible DST hour at a 150-second earliest delay is under 18,000). Longer/custom hot cadences may intentionally hit the ceiling and are disclosed. `Watch.max_attempts` stores `effectiveAttempts`; `Watch.attempts` remains the committed availability count.

Every schedule calculation works from UTC instants. `default_expiry` continues to construct midnight after the reservation date in `ZoneInfo(RESERVATION_TIMEZONE)` before converting to UTC, so 23/25-hour DST days are correct. A sampled delay is capped to remaining lifetime. If less than `MIN_DELAY_SECONDS` remains, the state machine creates/executes a deadline-expiration window without another provider call.

### Public API and Message Impact

- `Watch`, watch route request bodies, response JSON, enum values, and status codes remain unchanged.
- `POST /api/watches` adds documented response headers:
  - `X-Watch-Monitoring-Policy: deadline` or `attempt-limited`
  - `X-Watch-Max-Availability-Checks: <effective max_attempts>`
  - `Warning` with a short human-readable limitation only for `attempt-limited`.
- Creation is successful once the watch and immediate durable schedule marker commit. If the first broker publication then fails, the route still returns the preserved 201/body (avoiding an ambiguous client retry that could create another watch), records queue/recovery degradation, and leaves the due marker for bounded reconciliation. “Immediate dispatch” therefore means an immediate durable dispatch request plus a best-effort inline publication, not loss of the watch when the broker is unavailable.
- PromptRouter uses the same `AvailabilityPolicy` formatter. Deadline-capable watches keep the current “until a slot opens or the date passes” promise. Limited watches say “up to N availability checks; monitoring may stop before DATE.”
- `/health` adds `watch_queue`, `queue_readiness`, and `recovery_readiness`. Optional detail may state that process-local memory is not restart-durable and that broker reachability does not prove a worker is consuming.
- Internal Celery jobs add optional `window_id`; `monitor_watch(watch_id, window_id=None)` accepts old queued messages. Successful task return keys and meanings do not change.

### Data Models and Redis Key Schema

The public `Watch` document remains at `dibs:watch:{watch_id}`. Internal sidecars avoid exposing concurrency metadata or breaking old response fixtures.

```
WatchRuntime:
  schema_version: 2
  revision: bounded non-negative integer
  required_attempts: bounded positive integer
  supports_deadline: boolean
  consecutive_outages: bounded non-negative integer
  cadence_sequence: bounded non-negative integer
  window_id: string | null
  scheduled_for: UTC datetime | null
  phase: POLLING | BOOKING | null
  cancel_requested: boolean
  terminal_delete_at: UTC datetime | null
```

Redis keys:

- `dibs:watch:{id}` — public Watch JSON; no TTL while active, terminal expiry at retention deadline.
- `dibs:watch:{id}:runtime` — internal JSON; same terminal expiry.
- `dibs:watch:{id}:fence` — monotonic token counter; active lifetime, terminal expiry.
- `dibs:watch:{id}:claim:{window_hash}` — owner/token value with `PX=lease`.
- `dibs:watch:{id}:dispatch:{window_hash}` — dispatch owner with finite TTL.
- `dibs:watches` — existing all-watch set, maintained for compatibility.
- `dibs:watches:active` — existing active set.
- `dibs:watches:schedule` — sorted set, member is canonical hashed watch/window identity, score is due epoch milliseconds.
- `dibs:watches:terminal` — sorted set, member watch ID, score terminal deletion epoch milliseconds.
- `dibs:watch:event:{event_id}` and `dibs:watch:events` — bounded terminal-notification outbox/idempotency metadata.
- `dibs:recovery:leader` — finite compare-and-delete/renew leader lease.

Mock keys use `dibs:mock:`:

- `slot:{slot_id}` plus `slots:last_access` for bounded unbooked slot JSON/LRU ordering.
- `pin:{slot_id}` or `slots:pinned_until` for finite operation protection.
- `booking:{idempotency_hash}` for confirmation JSON.
- `booked:{slot_id}` for tombstone/booking reference.
- `bookings:expiry` for coordinated cleanup.

IDs used in key suffixes are generated or SHA-256 hashed before composition; arbitrary user text is never embedded. Cleanup/index operations use these indexes and bounded batches, never `KEYS` or an unbounded scan.

### Atomic Repository Interfaces

The protocol exposes state-machine operations rather than generic `save` for concurrent lifecycle changes:

```
create_with_schedule(watch, runtime) -> Created | AlreadyExists
claim_window(watch_id, window_id, owner_id, lease) ->
    Owned(watch, runtime, token) | Busy | Early | Stale | Terminal | Unknown
begin_booking(claim) -> BookingPermit | Cancelled | Fenced
commit_window(claim, observation, next_schedule) ->
    Committed(result, event_id?) | Fenced | Terminal | Unknown
cancel_if_active(watch_id, now) -> Watch | Unknown
expire_if_eligible(watch_id, expected_revision?, now) -> TransitionResult
claim_dispatch(marker, owner_id, lease) -> DispatchClaim | Busy | Stale
mark_dispatched(dispatch_claim, redispatch_after) -> boolean
release_dispatch(dispatch_claim) -> boolean
list_recovery_candidates() -> async iterator of decoded/prunable records
prune_index_member(watch_id, reason) -> None
cleanup_due(now, batch_size) -> CleanupResult
release_claim(claim) -> boolean
```

Redis implements each multi-key conditional operation with a registered Lua script (`EVALSHA`, safe reload on `NOSCRIPT`). A normal redis-py pipeline remains acceptable only for non-conditional reads. The scripts:

1. Read and decode the public/runtime documents.
2. Validate status, current `window_id`, expected revision, and owner/token.
3. Use Redis `TIME` for lease/ownership comparisons and bounded epoch-millisecond arguments for persisted schedules.
4. Apply document/runtime/index/schedule/event changes in one script invocation.
5. Return a small decision code plus required JSON; no script performs network I/O or work proportional to unchecked configuration.

Claim is one script: if the watch/window is current and due and no lease exists, `INCR` the fence, `SET claim owner|token PX lease NX`, and atomically move that window's schedule action time to the claim's lease expiry before returning the snapshot/revision. The marker is not deleted at claim time: while ownership is healthy, recovery sees it deferred until lease expiry; after a crash it becomes actionable again. Commit is another script: it requires the exact unexpired claim value and unchanged revision/window, increments revision once, removes the consumed marker, and either writes one next window plus one schedule ZSET member or one terminal state plus optional unique event. Cancellation/expiry scripts increment revision, clear current schedule/claim state, update indexes, and prevent a later claimed snapshot from committing. Recovery never synthesizes or redispatches a missing/current marker while an unexpired poll claim exists.

The in-memory repository stores `(Watch, WatchRuntime)`, leases, schedule markers, event IDs, and indexes under one `asyncio.Lock`. It uses the injected UTC clock for lease decisions and returns exactly the same decision types. It is equivalent within its process, not falsely advertised as cross-process durable.

### Poll State Machine and Cancellation Safety

Repository claim decisions are mapped without adding a public enum:

- `Unknown` → `UNKNOWN_WATCH`.
- A terminal document → `ALREADY_FINISHED`.
- `Busy`, `Early`, `Stale`, or `Fenced` → an internal duplicate/no-op disposition that `monitor_watch` serializes with existing outcome `ALREADY_FINISHED`, `retry_in_seconds=None`, and no provider, booking, notification, or enqueue. For task transport, this existing value means “this delivery has no remaining work”; the persisted Watch remains the authority for whether the overall watch is terminal.
- A committed normal miss → `NO_AVAILABILITY`; if its single increment reaches `max_attempts`, the same commit produces `EXPIRED` and no successor.
- Committed fulfillment/expiry retains `FOUND`, `BOOKED`, or `EXPIRED` exactly.

```
FUNCTION pollWindow(watchId, windowId, ownerId)
  claim := repository.claimWindow(watchId, windowId, ownerId, lease)
  IF claim IS Unknown THEN RETURN UNKNOWN_WATCH
  IF claim IS Terminal THEN RETURN ALREADY_FINISHED
  IF claim IS Busy OR claim IS Early OR claim IS Stale THEN RETURN DUPLICATE_NOOP

  TRY
    now := clock.utcNow()
    IF now >= claim.watch.expiresAt OR claim.watch.attempts >= claim.watch.maxAttempts THEN
      RETURN repository.commitWindow(claim, EXPIRE, no successor)
    END IF

    // One 55-second deadline covers search, optional booking permit, booking,
    // replay lookup, and preparation of the commit observation.
    WITH timeout(fullProviderSequenceTimeout)
      searchResult := adapter.searchAvailability(claim.watch.query)
      IF searchResult completed normally THEN localOutageCount := 0

      IF claim.watch.autoBook AND searchResult has slots THEN
        permit := repository.beginBooking(claim)
        IF permit IS CancelledOrFenced THEN RETURN DUPLICATE_NOOP
        bookingResult := adapter.bookWithStableIdempotencyKey(searchResult)
      END IF
    END WITH

    IF provider sequence ended with AdapterError THEN
      // Any earlier successful interaction reset the local count first, so an
      // immediately following booking error becomes outage 1.
      nextOutageCount := checkedIncrement(localOutageCount)
      delay := outageDelay(nextOutageCount, remainingLifetime)
      IF delay cannot satisfy antiHotPollFloor THEN
        RETURN repository.commitWindow(claim, EXPIRE, no successor)
      END IF
      RETURN repository.commitWindow(claim, OUTAGE_WITHOUT_ATTEMPT, one successor)
    END IF

    RETURN repository.commitWindow(
      claim,
      NORMAL_RESULT_WITH_EXACTLY_ONE_ATTEMPT_AND_OUTAGE_RESET,
      zero or one successor; expire atomically if new attempt = maxAttempts
    )
  CATCH CancelledError
    repository.releaseClaimIfOwned(claim) best effort
    RAISE
  FINALLY
    release only if this owner/token still owns an uncommitted lease
  END TRY
END FUNCTION
```

Auto-booking has an additional atomic `begin_booking(claim)` linearization point immediately before the irreversible provider call. It validates the claim/revision, changes the internal runtime phase from `POLLING` to `BOOKING`, and issues a one-use booking permit. Cancellation that wins before this point commits `CANCELLED`, revokes the claim, and prevents booking. Cancellation that arrives after the permit atomically records `cancel_requested` and waits up to the bounded full-provider/lease deadline instead of falsely returning `CANCELLED` while a booking may complete. The owner/recovery then resolves idempotently: an existing confirmation commits `BOOKED`; if the provider definitely produced no confirmation, it commits `CANCELLED` with no successor. If the owner crashes, recovery first calls `get_booking(watch:{watch_id})`; it commits `BOOKED` when found, otherwise it honors the pending cancellation before issuing any new booking permit. Thus a successful cancellation is never followed by a booking, while a cancellation that loses the documented race returns the concurrently completed `BOOKED` watch rather than lying.

The single 55-second timeout covers the full search-plus-optional-book sequence, not each call separately, and remains below Celery's 60-second soft limit. Commit commands use finite Redis socket timeouts and a 120-second lease leaves margin for commit/cleanup in both Celery and asyncio modes. `CancelledError` is never flattened. Atomic commit is run in a task protected with `asyncio.shield`; if outer cancellation arrives, the code awaits that one atomic command to a known result before re-raising. A process kill can still interrupt the command, but Redis then applies either all or none of the script.

The 120-second claim lease exceeds the 90-second hard task timeout, so another owner cannot take over while the original Celery task can still run. Asyncio mode is bounded by the same full-sequence timeout; neither mode renews a healthy claim beyond the lease. A former owner whose lease expired always fails commit, even if no new owner has yet committed.

A crash after external booking but before watch commit is recovered through the unchanged `watch:{watch_id}` idempotency key and shared booking repository. A crash after search can repeat a read-only provider call, but the same window can still commit an attempt only once.

### Schedule Marker, Dispatch, and Crash Windows

Creation and every committed active successor atomically write exactly one logical schedule marker. Queue publication is deliberately outside the repository transaction, because Redis state and a Celery broker cannot share one atomic commit. “At most once scheduled” is defined and tested as one current logical marker/window and one valid dispatch generation across startup contenders; a crash with unknowable broker acceptance may require another physical publication of that same generation, but single-flight prevents another provider cadence or successor.

Far-future work remains only in `dibs:watches:schedule`. Celery publication occurs when `scheduled_for <= now + WATCH_DISPATCH_HORIZON_SECONDS` (default five minutes), avoiding +7/+30-day ETA reservations and Redis broker visibility-timeout redeliveries. Startup still schedules future watches for their exact persisted due time by retaining the marker; the leader's bounded sweep publishes only as the horizon approaches. Asyncio may hold a local absolute-time wakeup, but the marker remains authoritative.

Dispatch protocol:

1. For a marker within the horizon, claim a finite dispatch generation/lease. Contending replicas cannot claim the same generation.
2. Call `TaskQueue.enqueue_watch_poll(watch_id, window_id, delay_seconds)` using a deterministic Celery `task_id` derived from the window/generation.
3. On broker acceptance, retain the marker and set its next action to `max(scheduled_for, now) + recovery_grace`. When the task claims the poll, the claim script moves that action time to the poll lease expiry; commit consumes/advances the marker.
4. On dispatch failure, release the dispatch lease (or let it expire), leave the marker due, log structured fields, mark queue/recovery degraded, and let worker retry/recovery try again.
5. If the process crashes after enqueue but before recording acceptance, reconciliation may publish a duplicate physical message because the two systems cannot prove acceptance atomically. Both carry the same `window_id`/generation; only one unexpired poll owner can call the provider and only one result/successor can commit. The design explicitly does not treat deterministic Celery `task_id` as broker deduplication.

Recovery skips/defer a marker while either its dispatch lease or poll claim is unexpired. It redispatches only after the corresponding action/lease time, and after owner death it reuses the same window rather than synthesizing a new cadence.

`AsyncioTaskQueue` keeps a dictionary keyed by `window_id`, so duplicate scheduling in one process reuses the existing task. Its tracked tasks use the current event loop, sleep until the absolute due time with recomputation after wake, and are cancelled/awaited on close. It never invokes `asyncio.run` inside the application loop.

### Provider Outage Backoff

`consecutive_outages` is internal runtime state. Search or auto-book `AdapterError` commits an outage result with no availability attempt increment. A provider interaction that returns normally—including empty availability—sets the counter to zero. Expected `SlotUnavailableError`/`SlotNotFoundError` races are handled as current normal booking races, not infrastructure outages.

```
FUNCTION outageDelay(n, interval, jitter, maximum, remaining, rng)
  exponentLimit := ceilLog2(maximum / interval)
  IF n - 1 >= exponentLimit THEN base := maximum
  ELSE base := checkedMultiply(interval, 2^(n - 1))
  END IF

  jittered := clamp(base + uniform(-jitter, +jitter), MIN_DELAY_SECONDS, maximum)
  IF remaining < MIN_DELAY_SECONDS THEN RETURN EXPIRE_WITHOUT_PROVIDER_CALL
  RETURN min(jittered, remaining)
END FUNCTION
```

The implementation saturates before exponentiation/multiplication, so a corrupt/large outage count cannot allocate or overflow. At the deadline marker, the state machine commits exact outcome `EXPIRED` without a provider call. A normal successful empty result returns to `PollSchedule.next_delay()` and increments one availability attempt.

### Shared Mock Booking State and Capacity

`MockBookingAdapter` continues deterministic slot generation but delegates state:

```
publish_and_filter(slots, operation_id, now) -> available slots
get_booking(idempotency_key, now) -> confirmation | None
book_slot(slot_id, idempotency_key, confirmation_factory, now) -> confirmation
release_operation(operation_id) -> None
cleanup(now, batch_size) -> counts
```

Redis Lua publication atomically skips active booked tombstones, touches already-admitted deterministic slots, and computes admission before writing new unbooked slots. It first evicts eligible oldest idle unbooked/unpinned entries, then admits new candidates in deterministic candidate order only up to the remaining capacity. If every existing entry is pinned/protected, new candidates are omitted from this search result rather than exceeding capacity or evicting protected state; a single query larger than capacity is deterministically truncated. Admitted/returned slots receive a finite operation pin. In-memory performs the same work under its lock, and a crashed pin expires automatically. Capacity counts generated unbooked records; protected booking/tombstone/idempotency records are governed separately by bounded retention.

Redis booking is one Lua script:

1. Return the existing idempotency confirmation if present.
2. Reject an active booked tombstone for another key as `SlotUnavailableError`.
3. Reject a missing generated slot as `SlotNotFoundError`.
4. Create one deterministic mock confirmation, booking record, and booked tombstone; remove the slot from unbooked/LRU state; index coordinated expiry.

Booking, idempotency, and tombstone protection share one `protected_until`. Coordinated cleanup removes the related records together only after that instant. Native TTLs are set to `protected_until + cleanup_grace` as a backstop, so an idempotency confirmation cannot remain protected after its tombstone disappears. The default/minimum protected retention is seven days. Later searches consult tombstones before publication and therefore cannot resurrect protected booked slots.

API `BookingService` and `WatchService`, and all Celery worker children, receive adapters over the selected shared repository. Redis mode uses the common client/prefix. Fallback mode constructs one in-memory mock repository per application process and injects it into every service in that process.

### Startup Recovery and Multi-Replica Coordination

Redis recovery acquires `dibs:recovery:leader` with `SET key owner NX PX lease`. Renewal and release use compare-owner Lua scripts; a replica stops scanning immediately if renewal fails. Per-window dispatch leases remain the final idempotency boundary. In-memory recovery uses a process-local `asyncio.Lock` and reports process-local scope.

For every active-index member:

- Missing/corrupt document or sidecar that cannot be migrated: remove stale active/all membership as appropriate, emit structured reason, continue.
- Terminal document in active index: remove active membership; ensure terminal cleanup index/TTL exists.
- `now >= expires_at` or `attempts >= max_attempts`: conditionally transition to `EXPIRED`, clear scheduling, create at most one expiry event.
- Future active window: preserve its absolute `scheduled_for` in the durable ZSET; publish to Celery only when it enters the bounded dispatch horizon, never immediately merely because startup ran.
- Due/overdue active window: dispatch immediately with its persisted window ID unless an unexpired dispatch lease or poll claim defers it.
- Active record with no marker and no live claim: synthesize one current marker conditionally from `next_check_at` (or now for a legacy immediate watch), never later than `expires_at`. A live claim is allowed to finish or expire before repair.

Each leader pass also runs bounded watch-terminal and mock-state cleanup batches. Healthy cleanup lag is bounded by the configured recovery sweep interval plus batch backlog; backlog keeps readiness degraded until drained. A failed dispatch or cleanup leaves its marker/index and records `watch_id`, `window_id`, owner, exception class, phase, and retry time without secrets. The coordinator releases its per-window dispatch lease and performs bounded follow-up sweeps while the application runs; another startup/replica can also resume after lease expiry. Individual failures do not abort remaining candidates. Readiness is `ready` only after a complete reconciliation/cleanup pass with no due backlog, `degraded` after any failed candidate/dispatch/cleanup or due backlog, and `unknown` before a pass or when no meaningful probe was performed.

### Terminal Notifications and Idempotency

The terminal transition script creates at most one event ID, deterministically `hash(watch_id, terminal_status, revision)`, only for `FOUND`, `BOOKED`, or `EXPIRED`. The notification boundary accepts that idempotency key. Idempotency-capable future transports must deduplicate it. For a sink without idempotency support, the dispatcher marks the event claimed/sent before invoking the sink, guaranteeing externally observable at-most-once behavior at the cost of a visibly recorded possible loss on crash. The current logging/recording implementations deduplicate event IDs in memory/repository tests.

Cancellation remains free of a new notification, preserving current behavior. Booking side effects use the independent stable provider idempotency key `watch:{watch_id}`.

### Terminal TTL, Index Pruning, and Cleanup

A terminal-transition Lua script atomically:

- writes terminal Watch/runtime state and clears `next_check_at`;
- removes active/schedule/claim/dispatch state;
- adds the due time to `dibs:watches:terminal`;
- applies matching absolute expiries to document/runtime/fence/event metadata;
- retains all-index membership through the retention window.

`cleanup_due` processes a bounded number of sorted-set members, verifies they are still terminal and due, then deletes document/sidecars and removes all/active/schedule/terminal membership in one Lua call. Native key expiry is a backstop; because Redis cannot atomically remove a set member when a key expires by TTL alone, `get`, list, save/migration, cleanup, and recovery all prune a missing/corrupt member immediately. Listings never return an invalid record. Active documents have no TTL.

### Health and Readiness

`watch_queue` always comes from the actual bound queue instance. `queue_readiness` is:

- `ready` for an open asyncio queue on the running loop, or after a successful finite Celery broker write-connection probe/dispatch;
- `degraded` after a performed probe/dispatch failure;
- `unknown` when Celery was merely importable/configured or no current probe exists.

A Redis ping may establish store reachability but is not by itself reported as worker consumption. If the broker is that Redis instance, health may report broker transport reachable only when the queue probe actually used the broker path; worker presence remains unknown unless separately checked. Recovery readiness follows completed reconciliation observations. Top-level status remains `ok` and HTTP 200 as required; details expose degradation without redefining the existing health meaning.

### Migration and Backward Compatibility

- Public Watch JSON remains schema-compatible. New concurrency fields live in a sidecar.
- On get/recovery, a missing sidecar invokes an atomic v1 migration. Migration never infers whether the persisted value 200 was a default or an operator's intentional ceiling: it preserves every legacy `max_attempts` exactly. For policy truth, it computes `remaining_required_total = existing attempts + 1 + ceil(remaining lifetime / earliest normal delay)` with checked arithmetic and sets only sidecar `supports_deadline = (persisted max_attempts >= remaining_required_total)`. Thus old active watches may remain intentionally/legacy limited, but no explicit ceiling is silently expanded. A separately documented offline/operator opt-in may raise selected active limits before rollout; it is never automatic. New watches receive the corrected default derivation. Attempts and all terminal states are preserved; terminal watches are never reopened.
- Legacy active records derive a deterministic initial `window_id` from watch ID, revision zero, and persisted `next_check_at`. Missing `next_check_at` uses now only for an active legacy record.
- `monitor_watch(watch_id)` remains accepted for already queued old jobs. It resolves the current window and still passes due/claim checks; new jobs always include `window_id`.
- Existing Redis key/index names remain readable. New code lazily prunes/migrates stale entries.
- Mixed old/new workers are not concurrency-safe because old code can still perform unfenced snapshot saves. Deployment therefore drains/stops old workers, deploys API/worker code from the same image version, runs startup migration/recovery, then starts consumers. Rollback likewise stops workers first. This operational gate is explicit rather than claiming unsafe rolling compatibility.
- No new runtime or PBT dependency is required. Property tests use deterministic exhaustive/model-based generators under pytest. The Redis script harness adds only an exact, implementation-verified `fakeredis[lua]` test pin to both dependency sources; no open range is permitted. If a future implementation chooses Hypothesis, it must likewise add one exact compatible test pin.

### Failure Handling

- **Redis unavailable during claim/commit/dispatch**: propagate only the recognized redis-py connectivity/timeout exceptions to the existing `monitor_watch` retry classifier. Never retry programming/validation/corrupt-state failures as infrastructure.
- **Broker dispatch failure after commit**: schedule marker remains; worker retry and recovery redispatch it. No state rollback is attempted.
- **Provider `AdapterError`**: commit outage/backoff, no availability attempt. It does not escape to Celery retry.
- **Task cancellation/soft timeout**: no snapshot save; release ownership if possible. If a booking permit was issued, persist/retain `cancel_requested` and recover through the provider idempotency key before declaring cancellation. A hard kill relies on lease expiry.
- **Lost lease/fenced commit**: return a terminal/stale no-op observation, do not notify/book/reschedule again.
- **Notification failure**: watch terminal state remains authoritative; event metadata records outcome and idempotency state. No watch reopening.
- **Corrupt persisted state**: fail closed for that record, prune it from active listings, log structured reason, and continue recovery. Do not reinterpret arbitrary JSON.
- **Cleanup failure**: data may live longer, never shorter; bounded next sweeps retry. API reads self-heal indexes.
- **Recovery leader loss**: stop current scan; per-window claims remain safe; another owner resumes after expiry.

### Security and Operability

- All timing/count inputs are bounded and all persisted datetimes are timezone-aware UTC.
- Redis scripts receive validated scalar arguments, use namespaced/hashed keys, and avoid unbounded scans. Script source is application-owned; external Redis values are treated as untrusted and validated before model use.
- The atomic multi-key layout supports standalone Redis and Sentinel-managed primary Redis, matching the current single Redis URL topology; Redis Cluster is not claimed. Startup checks `cluster_enabled`. If cluster mode is detected, the application refuses the Redis/Celery watch upgrade, records a degraded unsupported-topology reason, and uses the documented memory/asyncio fallback rather than executing cross-slot scripts or claiming distributed safety.
- `rediss://` remains supported. Redis credentials, OpenAI keys, booking details, and raw connection URLs are not logged. Structured errors use exception class and redacted endpoint identity.
- Owner IDs and fencing tokens are random/generated, never accepted from API clients.
- Metrics/logs should include claim result, stale/fenced count, outage count/backoff, schedule-marker age, recovery pass result, stale-index prune count, mock capacity/evictions, cleanup lag, and queue mode/readiness.
- Capacity/cleanup loops are batch-bounded. Terminal/mock retention defaults are seven days; active watches remain durable.
- Add root `Dockerfile` based on pinned Python 3.12, install the pinned project worker dependencies, copy only runtime sources, run as a non-root user, default to `python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000`, and define a finite stdlib HTTP health check.
- Add root `.dockerignore` excluding `.git`, `.venv`, caches, coverage, editor files, `.env*`, local Redis/Postgres data, tests, and spec artifacts not needed at runtime.
- Optionally add compose `api` and `worker` services behind an `app` profile, built from the same image. Worker command is `celery -A backend.workers.celery_app worker --loglevel=info`; API depends on healthy Redis and exposes 8000. Existing Redis/PostgreSQL definitions, volumes, ports, and health checks remain unchanged.
- README keeps PowerShell host commands and states that long-running API/worker commands must be run manually; automated validation uses one-shot commands only.

## Testing Strategy

### Validation Approach

Testing follows the bugfix two-phase discipline: first surface counterexamples and freeze existing behavior on unfixed code, then implement and run fix/preservation properties against both repository/queue modes. Tests inject clocks, deterministic jitter sources, queues, repositories, leader/lease stores, adapters, runners, and readiness probes. Redis atomicity tests execute the exact production Lua source and the real repository argument/result codecs in an in-process Redis/Lua test implementation (an implementation-selected compatible `fakeredis[lua]` release must be pinned exactly in both dependency manifests). They cover script errors, TTL units, malformed JSON, return decoding, and `NOSCRIPT` reload without a live Redis service; a hand-written fake pipeline alone is not accepted as atomicity evidence.

### Exploratory Bug Condition Checking

**Goal**: Reproduce each defect before production changes and confirm/refute the root-cause hypotheses.

**Test Plan**: Add characterization files for `monitor_watch`, `celery_app`, `main` upgrade/recovery wiring, repository concurrency, mock shared state, and policy math. Run new bug-condition assertions against unfixed code and record the expected failures. Run preservation assertions against unfixed code and require them to pass before implementation.

**Test Cases**:

1. **Lifetime Counterexamples**: same-day/+1/+7/+30 at 150-second delays show old limit 200 expires early.
2. **Duplicate Delivery Counterexample**: release concurrent `poll_once` calls from a barrier and observe multiple provider calls/successors.
3. **Cancellation Counterexample**: pause after read/provider call, cancel, then allow old snapshot save and observe overwrite risk.
4. **Commit/Enqueue Counterexample**: fail queue after save and show an active record with no recoverable marker.
5. **Cross-Adapter Mock Counterexample**: book in one adapter and show a second adapter still offers the slot/replay miss.
6. **Startup Counterexample**: seed due/future/expired/missing active entries and show `_attach_redis` schedules/prunes none.
7. **Outage Counterexample**: consecutive `AdapterError` increments attempts and remains at normal cadence.
8. **Retention/Health Counterexample**: terminal keys/all-index entries persist, stale IDs remain, and queue mode/readiness are absent.
9. **Worker Characterization**: success, each recognized retry, retry exhaustion, non-retry propagation, lock serialization, and cleanup from `watch-route-worker-retry-hardening`.
10. **Upgrade Branch Characterization**: Redis reachable/unreachable, Celery present/absent, selected repository/queue, immediate dispatch, shared adapter wiring, and shutdown with import/ping/queue doubles.

**Expected Counterexamples:**
- Attempt exhaustion precedes calendar expiry under supported defaults.
- More deliveries produce more provider calls and successors.
- A stale active snapshot can overwrite cancellation or terminal work.
- A durable state update can lose its only enqueue.
- Mock state and idempotency differ by adapter process.
- Startup ignores stranded active watches.
- Outages consume the availability budget without exponential pacing.
- Retention/readiness behavior is unbounded or overstated.

If a counterexample differs, update the root-cause analysis before implementation; do not weaken the expected behavior.

### Fix Checking

**Goal**: Verify all inputs satisfying `isBugCondition` satisfy the numbered correctness properties.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  result := executeFixedSystemWithDeterministicDoubles(input)
  ASSERT expectedBehavior(input, result)
END FOR
```

Run the exact exploration tests again after each corresponding phase. Repository model tests compare the real in-memory implementation and the exact-script Redis test implementation to a small sequential state-machine oracle. Concurrency tests use barriers/events rather than sleep-based timing.

### Preservation Checking

**Goal**: Verify all non-bug behavior remains equivalent to the unfixed baseline and the sibling hardening spec.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT observe(F(input)) = observe(F'(input))
END FOR
```

**Test Plan**: Freeze current API JSON, enums, immediate dispatch, default normal jitter, expiry conversion, fallback selection, `ConfigurationError`, booking key, deterministic slots, worker retries/result shape, runner cleanup, and compose services before edits. After each phase, run focused preservation tests and then the complete suite.

**Test Cases**:

1. Valid create returns current Watch shape and zero-delay first dispatch/marker.
2. Cancellation and all existing terminal/outcome paths retain exact strings.
3. Healthy empty searches use one 150–210 second successor.
4. DST and ordinary dates retain local-midnight expiry.
5. Redis/Celery missing/unreachable paths retain memory/asyncio operation and honest selection.
6. Stable watch booking key and deterministic mock slot identity remain unchanged.
7. Recognized Redis/Kombu failures retry with original exception, 60 seconds, max three; others propagate.
8. Runner/queue/Redis cleanup remains serialized, lazy, and idempotent.
9. Worker success returns exact keys and meanings.
10. Existing Redis/PostgreSQL compose definitions and Windows commands remain present.

### Unit Tests

- Policy ceil-division, default offsets, short ceilings, huge/malformed bounds, and truthful formatter/header values.
- `PollSchedule` normal/outage jitter, saturation, floor, cap, reset, and deadline clipping.
- WatchRuntime v1 migration and public Watch projection.
- Every repository decision code, revision/token check, terminal monotonicity, booking-permit/cancel-request race, no-op outcome mapping, final-attempt expiry, index prune, TTL cleanup, and schedule marker transition for memory and exact Lua-backed fake Redis.
- Queue window deduplication, bounded dispatch horizon, deterministic task arguments/ID, cancellation, close, and dispatch failure release.
- Recovery candidate classification, live-claim deferral, leader renewal/loss, partial failure, structured status, terminal/mock cleanup cadence, and asyncio future scheduling.
- Mock publish/filter/book/replay/pin/admission/evict/cleanup operations, including capacity one, an all-pinned repository, a query larger than capacity, concurrent publication, and seven-day boundaries.
- Health readiness state derivation from performed/not-performed probes.
- Dockerfile/.dockerignore/compose static assertions.

### Property-Based Tests

Use deterministic exhaustive tables plus fixed-seed model generators (no new dependency required):

- Generate remaining lifetimes, timezone/DST dates, interval/jitter pairs, ceilings, and jitter sequences; assert Property 1.
- Generate duplicate counts and operation interleavings over claim, search, pre-booking permit, cancel request, booking, expire, provider result, lease expiry, takeover, commit, dispatch, and notification; compare both repositories to the oracle and assert Property 3.
- Generate multi-adapter slot/search/booking/idempotency/cleanup interleavings at capacity, all-protected admission, and retention edges; assert Property 4.
- Generate recovery indexes containing every valid/invalid state and multiple coordinators/failures; assert Property 5.
- Generate terminal transitions and cleanup times around exact TTL boundaries; assert Property 8.
- Generate health selections/probe histories; assert Property 9.
- Generate outage/success sequences, large consecutive counters, jitter extrema, and remaining lifetimes; assert Property 10.
- Generate non-bug inputs across all existing statuses/outcomes and retry exception partitions; assert Property 2.

Each generated run has a bounded operation count and prints the seed/operation trace on failure so it is reproducible.

### Integration Tests

- FastAPI lifespan with fake Redis reachable/unreachable and worker import present/absent: assert final shared adapter repository, queue/store selection, immediate dispatch, recovery, health, and shutdown.
- Direct decorated `monitor_watch.run` with service/runner/queue doubles: success, recognized retry, retry exhaustion, cancellation, old one-argument jobs, duplicate windows, and unchanged result shape.
- Two service instances over one repository plus two queue instances: duplicate claim, cancellation before booking permit, cancellation after permit, lease expiry/takeover, recovery during a live claim, lost dispatch, final-attempt expiry, and terminal event cardinality.
- API and worker adapter instances over one exact-script fake Redis mock repository: atomic distinct-key race, cross-instance replay, later search exclusion, protected-capacity admission, and cleanup.
- Recovery with due/future/far-future/expired/exhausted/missing/corrupt/terminal records, two leaders, live claims, dispatch failure, cleanup backlog, and owner death.
- Full existing pytest suite plus the sibling `watch-route-worker-retry-hardening` tests using one-shot `python -m pytest` commands.
- `python -m compileall backend tests`, `git diff --check`, and optional one-shot `docker build` / `docker compose -f infra/docker-compose.yml config` when Docker is available. No development server, worker, watcher, or interactive command is started by validation.
