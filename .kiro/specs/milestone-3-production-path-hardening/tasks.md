# Implementation Plan

- [ ] 1. Write production-path bug-condition characterization tests before changing production code
  - **Property 1: Bug Condition** - Production Path Lifetime, Concurrency, Recovery, Shared State, and Operability Defects
  - **CRITICAL**: Write and run these tests against the unfixed code before editing production modules. These assertions encode `expectedBehavior`; do not weaken them or fix production code during this task.
  - Add deterministic/fixed-seed characterization coverage in focused files such as `tests/test_watch_policy.py`, `tests/test_watch_repository_state_machine.py`, `tests/test_watch_dispatcher.py`, `tests/test_watch_recovery.py`, `tests/test_mock_booking_state.py`, `tests/test_main_watch_wiring.py`, `tests/test_monitor_watch.py`, and `tests/test_watch_operability.py`; use bounded exhaustive/model generators under pytest, barriers/events instead of sleeps, and fakes/doubles instead of live Redis, Celery, a broker, a provider, or a development server.
  - Exercise `isBugCondition(input)` from the design for same-day, +1-day, +7-day, and +30-day watches at the earliest default 150-second delay; show that the old fixed 200-attempt policy can expire before `expires_at`, that the old router makes an unqualified deadline promise, and that malformed/huge attempt values lack checked bounds. Assert the corrected allowance `1 + ceil(remaining_lifetime / earliest_normal_delay)`, immediate first check, finite ceiling, and truthful limited-policy messaging/headers.
  - Release two and four same-window `poll_once` calls through a barrier and show old behavior can make multiple provider calls, attempt commits, successor enqueues, or terminal side effects. Pause work around provider/save/enqueue boundaries to expose stale cancellation overwrite, terminal overwrite, booking replay risk, lost commit-to-enqueue work, and the absence of finite lease takeover/fencing.
  - Construct two mock adapters representing API/worker processes, race distinct booking keys for one slot, replay one key across adapters, search after booking, overlap search/book, and generate beyond a small capacity. Show process-local confirmations/booked state, unsynchronized publication, and unbounded generated slots violate the shared atomic repository contract.
  - Seed due, future, far-future, expired, attempt-exhausted, missing, corrupt, terminal-in-active, and active-without-marker records; run current startup wiring with two contenders and injected dispatch failure. Show there is no leader/per-window recovery ownership, no durable schedule repair, no bounded follow-up, and no stale-index reconciliation.
  - Generate consecutive search and auto-book `AdapterError` sequences, provider-sequence timeouts, a later normal empty result, jitter extrema, and short remaining lifetimes. Show old behavior increments availability attempts and keeps normal cadence; assert corrected no-attempt capped exponential backoff, reset-after-success, anti-hot-poll floor, deadline clipping, and exact `EXPIRED` result.
  - Seed terminal watches and stale all/active index members around retention boundaries; show terminal documents/index membership persist without bounded cleanup and reads do not self-heal. Exercise `/health` selections/probe histories and show the selected queue and evidence-based queue/recovery readiness are absent.
  - Characterize `monitor_watch.py`, `celery_app.py`, and `main._attach_redis`/lifespan branches: task success, recognized Redis/Kombu retry, retry exhaustion, non-retry propagation, runner serialization, lazy/idempotent cleanup, Celery configuration, Redis reachable/unreachable, Celery present/absent, actual queue/store selection, immediate first dispatch, and shared adapter wiring. Reuse or remain compatible with all assertions from `.kiro/specs/watch-route-worker-retry-hardening`; worker-only imports must remain skippable when the worker extra is absent.
  - Add static counterexamples showing the repository lacks unattended API/worker image assets and truthful migration/deployment guidance; do not start an API server, worker, watcher, Redis, or broker.
  - Run only one-shot focused pytest commands. **EXPECTED PRE-FIX OUTCOME**: each defect-reproducing assertion fails for the documented old behavior and records a reproducible counterexample/operation trace; if an expected counterexample does not reproduce, update the bug analysis before implementation rather than weakening the assertion.
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.13, 2.14, 2.15_

- [ ] 2. Write preservation property tests before changing production code
  - **Property 2: Preservation** - Existing Public Watch, Worker Retry, Mock Identity, and Local-Development Contracts
  - **IMPORTANT**: Follow observation-first methodology. Observe the unfixed implementation, encode those outputs without assuming new behavior, and make this suite pass before implementing any fix.
  - Freeze `POST /api/watches` request/query/status/body behavior and public `Watch` JSON, immediate zero-delay first dispatch, list/read behavior, active cancellation with cleared `next_check_at`, exact public statuses `ACTIVE`, `FOUND`, `BOOKED`, `EXPIRED`, `CANCELLED`, and exact outcomes `NO_AVAILABILITY`, `FOUND`, `BOOKED`, `EXPIRED`, `ALREADY_FINISHED`, `UNKNOWN_WATCH`.
  - Freeze healthy empty-provider behavior as one successor in the configured jitter window, including 150–210 seconds for 180 ± 30, and freeze midnight-after-reservation-date expiry in `RESERVATION_TIMEZONE`, including ordinary and DST 23/25-hour cases with no work after the UTC deadline.
  - Freeze retained `ConfigurationError` identity/HTTP 503 precedence before date validation or creation; memory/asyncio fallback when Redis or Celery is absent/unreachable; actual `watch_store`; and current top-level `/health` HTTP 200, `status`, `service`, and `config` semantics.
  - Freeze the exact watch booking key `watch:{watch_id}`, deterministic mock slot IDs/availability/capacities, `MOCK_CONFIRMED` identity, and protected same-adapter idempotent replay without implying a real-provider guarantee.
  - Import and preserve the sibling hardening contract: retry only aliased redis-py connection/timeout and Kombu operational failures with the original exception, `countdown=60`, `max_retries=3`, traceback logging, immediate propagation of all other failures, persistent-runner serialization, lazy/idempotent Redis/queue/runner cleanup, optional worker imports, and exact successful task keys `{watch_id, outcome, retry_in_seconds}` with string outcome.
  - Freeze existing Redis/PostgreSQL compose service definitions, ports, volumes, and health behavior, plus Windows-friendly host commands. Tests and snapshots must tolerate only the additive documented creation headers and health fields introduced later; they must reject changes to public watch JSON/enums or replacement of existing infrastructure.
  - Run the preservation files with one-shot pytest commands and no live services. **EXPECTED PRE-FIX OUTCOME**: all preservation tests pass on the unfixed code and establish the baseline that every later phase must rerun unchanged.
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13, 3.14, 3.15_

- [ ] 3. Add bounded watch settings and a checked, truthful lifetime policy

  - [ ] 3.1 Bound and cross-validate all watch/runtime settings in `backend/config.py`
    - Raise the default `WATCH_MAX_POLL_ATTEMPTS` safety ceiling to 25,000 and accept only 1–1,000,000; reject overlong integer text before conversion, unsupported signs, non-finite/invalid values, and inconsistent combinations without looping or allocating proportional to input.
    - Add the design settings with exact finite bounds: claim lease default 120 seconds (91–3,600), provider-sequence timeout 45 (1–45), repository commit timeout 5 (1–10), outage cap 3,600 (at least the normal interval and at most 86,400), terminal retention 604,800 (3,600–31,536,000), recovery leader lease 30 (5–300), recovery sweep 30 (5–3,600), dispatch horizon 300 (30–3,600), mock slot capacity 10,000 (1–100,000), mock idle TTL 3,600 (60–604,800), and mock booking retention 604,800 (604,800–31,536,000).
    - Centralize the Celery 60-second soft/90-second hard limits and five-second overhead reserve, then enforce `provider timeout + commit timeout + reserve < 60 < 90 < claim lease`; keep interval at 15–3,600 and require `0 <= jitter < interval`.
    - Preserve `ConfigurationError` precedence and the existing environment names; fail before service creation rather than silently substituting defaults.
    - _Bug_Condition: unbounded or inconsistent cadence, ceiling, timeout, lease, backoff, retention, and capacity configuration can overflow, hot-poll, or diverge from worker limits_
    - _Expected_Behavior: validated finite settings satisfy the documented bounds and strict timing inequality with checked arithmetic_
    - _Preservation: valid existing environment values and request-time configuration failure semantics remain unchanged_
    - _Requirements: 2.3, 2.6, 2.8, 2.10, 2.13, 2.15, 3.7, 3.11_

  - [ ] 3.2 Implement `AvailabilityPolicyFactory` in `backend/services/watch_policy.py`
    - Derive remaining lifetime from aware UTC instants and integer microseconds; use checked ceil-division and checked addition for `required_attempts = 1 + ceil(remaining / earliest_delay)` and `effective_attempts = min(required, safety_ceiling)`.
    - Return bounded policy facts (`required_attempts`, `effective_attempts`, `supports_deadline`, limiting reason) plus one formatter shared by API headers and PromptRouter.
    - Cover same-day/+1/+7/+30, DST-short/long days, zero remaining lifetime, earliest jitter extremes, custom hot cadences, and ceiling-limited policies. Never enumerate attempts or schedule after `expires_at`.
    - Keep `default_expiry` as local midnight after the reservation date converted to UTC, and keep the first check immediate/un-jittered.
    - _Bug_Condition: static attempts can end default watches before their calendar deadline or arithmetic can wrap/truncate_
    - _Expected_Behavior: checked lifetime derivation makes supported defaults deadline-capable while retaining a finite intentional safety ceiling_
    - _Preservation: immediate first check, timezone-derived expiry, and public `Watch.max_attempts`/`attempts` meanings remain intact_
    - _Requirements: 2.1, 2.2, 2.3, 3.1, 3.4, 3.5_

  - [ ] 3.3 Apply truthful policy messaging without changing public watch JSON
    - Make watch creation store `effective_attempts` and expose policy metadata internally to the route/router without adding fields to the `Watch` response model.
    - In `backend/api/routes/watches.py`, add only `X-Watch-Monitoring-Policy`, `X-Watch-Max-Availability-Checks`, and an attempt-limited `Warning`; retain the existing body, status 201, request/query contract, validation order, and immediate first-dispatch contract. The durable marker implementation remains explicitly deferred to tasks 4–5.
    - In `backend/orchestrator/router.py`, reuse the same formatter: keep the current deadline promise only when `supports_deadline`, otherwise state “up to N availability checks” and that monitoring may stop before the reservation date.
    - _Bug_Condition: an intentionally short safety ceiling is accepted while API/router text claims unqualified monitoring until the date_
    - _Expected_Behavior: every creation surface tells the truth about the effective finite policy_
    - _Preservation: deadline-capable wording, public JSON/enums, HTTP status, and immediate first check remain unchanged_
    - _Requirements: 2.1, 2.2, 3.1, 3.3, 3.5_

  - [ ] 3.4 Re-run the same policy/messaging characterization and preservation tests
    - **Property 1: Expected Behavior** - Re-run the task 1 lifetime, bounds, header, and router assertions; the former counterexamples must now pass without replacing their assertions.
    - **Property 2: Preservation** - Re-run the task 2 API JSON, enums, immediate dispatch, timezone expiry, configuration precedence, and healthy jitter assertions unchanged.
    - Use one-shot focused pytest commands only. **EXPECTED OUTCOME**: corrected policy tests pass and all selected preservation tests remain green.
    - _Requirements: 2.1, 2.2, 2.3, 2.11, 3.1, 3.3, 3.4, 3.5, 3.7_

  - _Requirements: 2.1, 2.2, 2.3, 2.11, 3.1, 3.3, 3.4, 3.5, 3.7_

- [ ] 4. Introduce `WatchRuntime` and equivalent atomic repository state machines

  - [ ] 4.1 Add the internal `WatchRuntime` sidecar and bounded decision models
    - Create `backend/models/watch_runtime.py` with schema version 2, revision, policy facts, outage count, cadence sequence/current `window_id`/`scheduled_for`, `POLLING`/`BOOKING` phase, `cancel_requested`, and terminal cleanup time; validate bounded counts and aware UTC datetimes.
    - Add typed claim, booking permit, commit, transition, dispatch, cleanup, and reconciliation decision enums/models without adding public `WatchStatus` or `WatchPollOutcome` members.
    - Implement atomic v1 sidecar migration: preserve every legacy `max_attempts`, attempts, terminal state, and public document; compute truthful remaining policy support with checked arithmetic; derive a deterministic initial window from watch ID/revision/persisted due time; never reopen terminal watches or silently raise legacy ceilings.
    - Add model/projection tests proving public Watch JSON and exact enum values are byte/structure compatible apart from separately documented response headers.
    - _Bug_Condition: public snapshots contain no revision/window/policy/lease metadata and legacy records cannot enter the coordinated protocol safely_
    - _Expected_Behavior: bounded internal sidecars support migration and coordination without exposing new public fields_
    - _Preservation: all public statuses, outcomes, attempts, terminal states, and legacy limits remain unchanged_
    - _Requirements: 2.1, 2.3, 2.4, 2.5, 2.6, 2.9, 2.13, 3.1, 3.3, 3.5_

  - [ ] 4.2 Expand `WatchRepository` and implement the in-memory atomic protocol
    - In `backend/db/repositories/watches.py`, retain compatible query/list entry points and add the design operations: atomic create-with-schedule, claim/release window, begin booking, fenced commit, conditional cancel/expire, claim/mark/release dispatch, recovery iteration/pruning, and bounded cleanup.
    - Implement `InMemoryWatchRepository` with watches/runtimes, monotonic fence counters, expiring poll and dispatch leases, schedule/terminal indexes, and terminal event IDs under one `asyncio.Lock` and an injected UTC clock.
    - Enforce one current logical marker, one availability-attempt commit per window, no attempt for outages/crashes before commit, terminal monotonicity, stale revision/token rejection, no takeover before lease expiry, takeover after expiry, and exact parity of decision codes expected from Redis.
    - Keep reads sorted and public, active records durable, cancellation idempotent, and missing/terminal/no-op outcomes mappable to existing public enums.
    - _Bug_Condition: whole-document replacement permits duplicate polling, lost progress, stale overwrite, nonrecoverable work, and permanent/early takeover_
    - _Expected_Behavior: one locked state-machine transition linearizes every lifecycle decision and fences stale owners_
    - _Preservation: query/list/cancel behavior and exact public terminal/outcome contracts remain compatible_
    - _Requirements: 2.4, 2.5, 2.6, 2.9, 2.13, 3.2, 3.3, 3.5_

  - [ ] 4.3 Implement exact production Redis Lua operations and script codecs
    - Put application-owned Lua sources/codecs in `backend/db/repositories/watch_scripts.py` (or an equivalently isolated production module) and invoke them from `RedisWatchRepository` via registered scripts/`EVALSHA` with safe `NOSCRIPT` reload; use pipelines only for non-conditional reads.
    - Implement exact atomic create/migrate, claim/fence/defer marker, begin-booking, commit successor/terminal/event, cancel, expire, dispatch lease, leader lease helper, prune, and cleanup operations over the documented watch/runtime/fence/claim/dispatch/schedule/terminal/event keys.
    - Validate status, canonical hashed window identity, revision, owner/token, and Redis `TIME`; use bounded epoch-millisecond arguments, finite PX leases, matching terminal expiries, one schedule marker/event, and small decision responses. Never embed arbitrary user text, call network I/O from scripts, use `KEYS`, or scan/work proportional to unchecked configuration.
    - Exercise the exact production script text and real argument/result codecs in an in-process Lua-capable Redis test harness, including malformed JSON, TTL units, stale tokens, lease expiry, script errors, and `NOSCRIPT` reload. A hand-written fake pipeline is not sufficient evidence.
    - Add `fakeredis[lua]==<implementation-verified-version>` to the `test` extra in `pyproject.toml` and the test section of `requirements.txt` **only if** the exact-script harness is required because existing dependencies/doubles cannot execute the production Lua. Before pinning, verify one exact release against Python 3.12, `redis==7.4.1`, and the scripts; use the identical exact pin in both manifests, no open range, and do not add Hypothesis or a runtime dependency.
    - _Bug_Condition: unguarded Redis pipelines do not provide compare-and-set semantics for multi-key lifecycle changes_
    - _Expected_Behavior: exact scripts make Redis decisions atomic and behaviorally equivalent to the in-memory protocol_
    - _Preservation: existing key/index names remain readable, `rediss://` remains supported, and recognized redis-py failures still reach the sibling retry classifier unchanged_
    - _Requirements: 2.3, 2.4, 2.5, 2.6, 2.9, 2.10, 2.13, 2.11, 3.6, 3.11, 3.13_

  - [ ] 4.4 Add a bounded sequential oracle and compare both repository implementations
    - Generate fixed-seed bounded traces over create, claim, duplicate claim, begin booking, cancel, expire, outage/normal/terminal commit, lease expiry, takeover, stale commit, dispatch, event, and cleanup operations.
    - Compare every in-memory and exact-Lua decision/state projection with a small sequential oracle; print the seed and operation trace on failure and use barriers/events for true concurrency cases.
    - Cover final-attempt expiry, crash-before-provider/no-attempt, possible post-provider replay with same window, one terminal event ID, owner loss, and invalid persisted input.
    - _Bug_Condition: repository implementations can diverge or accept forbidden stale/concurrent transitions_
    - _Expected_Behavior: both implementations satisfy the same fenced single-flight model for every generated trace_
    - _Preservation: public reads/outcomes and retryable infrastructure exception identities are unchanged_
    - _Requirements: 2.4, 2.5, 2.6, 2.11, 2.13, 3.2, 3.3, 3.11, 3.13_

  - [ ] 4.5 Re-run the same repository characterization and preservation tests
    - **Property 1: Expected Behavior** - Re-run task 1 duplicate-delivery, stale-cancel, lost-enqueue, lease, and atomicity cases against both repositories; old failures must now pass.
    - **Property 2: Preservation** - Re-run task 2 public model/enums, cancellation, terminal mapping, Redis optionality, and worker retry exception-partition tests unchanged.
    - Use one-shot focused pytest commands only. **EXPECTED OUTCOME**: model/concurrency checks pass for memory and exact Lua while preservation remains green.
    - _Requirements: 2.4, 2.5, 2.6, 2.11, 2.13, 3.2, 3.3, 3.6, 3.11, 3.13_

  - _Requirements: 2.4, 2.5, 2.6, 2.9, 2.10, 2.11, 2.13, 3.1, 3.2, 3.3, 3.5, 3.6, 3.11, 3.13_

- [ ] 5. Add cadence-window task arguments, durable dispatch, and queue deduplication

  - [ ] 5.1 Extend `TaskQueue` with internal cadence identity while preserving old jobs
    - Change `enqueue_watch_poll` to carry `watch_id`, `window_id`, and an absolute due/delay value needed by the dispatcher; update recording doubles without exposing these internals in API JSON.
    - Keep `monitor_watch(watch_id, window_id=None)` compatible with old one-argument queued jobs; new publications always include the canonical current window.
    - Derive deterministic Celery `task_id` from canonical hashed watch/window/dispatch generation, but do not claim broker-level deduplication from that ID.
    - _Bug_Condition: jobs carry only watch ID, so redeliveries cannot share one logical cadence identity_
    - _Expected_Behavior: every physical delivery identifies the same persisted logical window and old queued messages remain resolvable_
    - _Preservation: successful worker return keys/meanings and optional Celery import behavior remain unchanged_
    - _Requirements: 2.4, 2.6, 2.9, 2.11, 3.6, 3.12, 3.15_

  - [ ] 5.2 Make `AsyncioTaskQueue` window-deduplicated and lifecycle-safe
    - Track one task per `window_id`, recompute sleeps from an injected absolute clock after wakes, execute on the current loop, and replace/remove entries only when the repository authorizes a new generation.
    - On close, mark closed, cancel and await all pending tasks once, and preserve exception isolation without calling `asyncio.run` inside the application loop.
    - Report queue readiness from actual loop/open state and keep process-local durability limitations explicit.
    - _Bug_Condition: duplicate asyncio scheduling creates multiple local tasks and restart behavior has no authoritative marker_
    - _Expected_Behavior: one local task represents each repository window and cleanup is bounded/idempotent_
    - _Preservation: memory/asyncio fallback remains available without external infrastructure_
    - _Requirements: 2.4, 2.9, 2.10, 2.11, 2.14, 3.6, 3.12_

  - [ ] 5.3 Implement `WatchScheduleDispatcher` in `backend/workers/dispatcher.py`
    - Dispatch only repository schedule markers within `WATCH_DISPATCH_HORIZON_SECONDS`; keep far-future work durable and compute the next wake from horizon entry, sweep interval, leader renewal, and marker-change events so a short horizon cannot dispatch late.
    - Claim a finite per-window dispatch generation/lease, enqueue with the canonical window/generation, mark broker acceptance by deferring marker action to recovery grace, and release/expire ownership on failure while leaving the marker recoverable.
    - Reuse the same logical window after uncertain broker acceptance; permit duplicate physical publication but rely on poll single-flight to prevent duplicate provider work/successors.
    - Emit structured, redacted failure details and readiness observations; do not roll back an already committed watch transition.
    - _Bug_Condition: state commit and broker enqueue are separate failure domains with no durable independently dispatchable marker_
    - _Expected_Behavior: one persisted schedule survives crashes and can be safely redispatched under a finite lease_
    - _Preservation: the first dispatch request remains immediate and normal misses retain one jittered successor intent_
    - _Requirements: 2.4, 2.6, 2.9, 2.10, 2.11, 3.1, 3.4, 3.6_

  - [ ] 5.4 Re-run the same queue/dispatch characterization and preservation tests
    - **Property 1: Expected Behavior** - Re-run task 1 commit/enqueue crash, duplicate publication, horizon, dispatch failure, and old-job cases using queue/repository doubles.
    - **Property 2: Preservation** - Re-run task 2 immediate first check, healthy one-successor jitter, fallback queue, exact worker result, and queue/runner cleanup tests unchanged.
    - Use one-shot focused pytest commands only. **EXPECTED OUTCOME**: durable marker/deduplication assertions pass with no change to preserved public behavior.
    - _Requirements: 2.4, 2.6, 2.9, 2.10, 2.11, 3.1, 3.4, 3.6, 3.12, 3.15_

  - _Requirements: 2.4, 2.6, 2.9, 2.10, 2.11, 3.1, 3.4, 3.6, 3.12, 3.15_

- [ ] 6. Convert `WatchService` to the fenced cadence-window state machine

  - [ ] 6.1 Migrate creation, compatibility polling, and atomic normal commits
    - Make `WatchService.create` derive policy, persist `Watch` + `WatchRuntime` + immediate schedule marker atomically, then request best-effort inline dispatch. Return the preserved 201/body even if broker publication fails after durable creation, and surface degradation for recovery.
    - Add `poll_window(watch_id, window_id, owner_id)` that claims before provider work and maps Unknown/Terminal/Busy/Early/Stale/Fenced to existing `UNKNOWN_WATCH` or no-op `ALREADY_FINISHED` without provider, booking, notification, attempt, or successor side effects.
    - Keep `poll_once(watch_id)` as a compatibility wrapper that resolves the repository-authoritative current window; a completed normal provider result commits exactly one attempt and either one successor or one exact terminal outcome.
    - Cap every normal delay to remaining lifetime; when less than the anti-hot-poll floor remains, commit a deadline-expiration window without another provider call.
    - _Bug_Condition: snapshot polling and independent rescheduling allow duplicate work, stale saves, lost progress, and work after expiry_
    - _Expected_Behavior: claim-first processing and one fenced commit produce at most one attempt/successor/terminal transition per cadence window_
    - _Preservation: exact outcomes, attempt-ceiling `EXPIRED`, immediate first check, normal jitter, and deadline semantics remain unchanged_
    - _Requirements: 2.1, 2.4, 2.5, 2.6, 2.9, 3.1, 3.3, 3.4, 3.5_

  - [ ] 6.2 Add bounded provider sequencing and outage backoff
    - Apply one owned `asyncio` timeout to search, booking permit, booking/reconciliation, and commit preparation; translate only expiry of that owned deadline to `ProviderSequenceTimeout(AdapterError)`.
    - Never catch outer `CancelledError`, Celery soft/hard termination, programming/validation/state failures, or expected slot races as provider outages. Shield and await the one bounded atomic commit before re-raising outer cancellation.
    - Store saturating `consecutive_outages`; on search or auto-book `AdapterError`, increment outage count with checked saturation, consume no availability attempt, and schedule `interval * 2^(n-1)` capped before exponentiation/multiplication, jittered within floor/maximum and clipped to deadline.
    - Reset outage count after any normal provider interaction, including empty availability; a subsequent normal miss uses the ordinary jitter and exactly one attempt. Expire without a provider call when less than the floor remains.
    - _Bug_Condition: adapter outages consume user attempts, retry at ordinary cadence, or can overflow/hot-poll; broad timeout handling can misclassify task cancellation_
    - _Expected_Behavior: outage windows preserve the availability budget and use finite capped exponential pacing until success/deadline_
    - _Preservation: normal empty results, expected slot races, outer cancellation, and sibling retry classification retain their meanings_
    - _Requirements: 2.1, 2.3, 2.5, 2.6, 2.15, 3.3, 3.4, 3.5, 3.11_

  - [ ] 6.3 Fence auto-booking, cancellation, and terminal events
    - Add `ReservationAdapter` authoritative reconciliation by stable key/booking permit with tri-state `CONFIRMED`, `DEFINITIVELY_ABSENT`, or `UNKNOWN`; keep ordinary `get_booking(...)=None` from being treated as definitive after a permit.
    - Linearize `begin_booking` before the irreversible call. Cancellation that wins first commits `CANCELLED`; cancellation after permit records `cancel_requested` and resolves to confirmed `BOOKED`, authoritative `CANCELLED`, or bounded unknown reconciliation without issuing a second booking identity.
    - Keep the exact provider key `watch:{watch_id}` across crash recovery. Reject stale owner/revision commits and ensure terminal statuses cannot be reopened or overwritten.
    - Create deterministic terminal event IDs only for `FOUND`, `BOOKED`, and `EXPIRED`; deduplicate idempotent sinks, or claim/mark non-idempotent sinks before invocation for externally observable at-most-once behavior. Cancellation gains no notification.
    - _Bug_Condition: cancellation/terminal races permit stale ACTIVE overwrite, repeated booking, misleading cancellation, or duplicate terminal notification_
    - _Expected_Behavior: fenced permits/reconciliation make booking and terminal side effects monotonic and idempotent_
    - _Preservation: stable booking key, exact statuses/outcomes, cancellation semantics, and mock confirmation behavior remain public-compatible_
    - _Requirements: 2.5, 2.6, 2.7, 2.11, 3.2, 3.3, 3.8, 3.9_

  - [ ] 6.4 Migrate `monitor_watch` and worker construction without regressing sibling hardening
    - Accept optional `window_id`, invoke the window-aware service path on the persistent serialized runner, and keep old one-argument messages resolving current state.
    - Build the service over the final atomic Redis watch repository and dispatcher; retain adapter injection through this phase, then let task 8 rebind worker/API adapters to the shared mock-state repository once that boundary exists. Keep lazy caches and idempotent resource cleanup without constructing unused resources.
    - Preserve the explicit recoverable tuple from `watch-route-worker-retry-hardening`, original exception identity, 60-second countdown, maximum three retries, immediate propagation of non-transient failures, and exact success dictionary keys/meanings.
    - Keep Celery late acknowledgment, prefetch one, 60/90-second limits, JSON serializers, UTC/timezone behavior, and worker-only optional imports characterized.
    - _Bug_Condition: production worker entry points bypass cadence identity/shared state or broaden retries while the service becomes coordinated_
    - _Expected_Behavior: worker deliveries participate in the same fenced protocol without changing retry/resource/result contracts_
    - _Preservation: every accepted behavior from `watch-route-worker-retry-hardening` remains green_
    - _Requirements: 2.4, 2.6, 2.7, 2.11, 3.6, 3.11, 3.12, 3.15_

  - [ ] 6.5 Re-run the same state-machine/outage characterization and preservation tests
    - **Property 1: Expected Behavior** - Re-run task 1 duplicate windows, cancellation interleavings, lease takeover, provider timeout, outage sequences, booking replay, and terminal event cases; do not create replacement post-fix tests.
    - **Property 2: Preservation** - Re-run task 2 statuses/outcomes, healthy jitter, deadline, stable key, worker retry/result, runner serialization, and cleanup tests unchanged.
    - Use one-shot focused pytest commands only. **EXPECTED OUTCOME**: every state-machine counterexample passes and the sibling/preservation suite stays green.
    - _Requirements: 2.4, 2.5, 2.6, 2.11, 2.15, 3.2, 3.3, 3.4, 3.5, 3.8, 3.11, 3.12, 3.15_

  - _Requirements: 2.1, 2.4, 2.5, 2.6, 2.7, 2.9, 2.11, 2.15, 3.1, 3.2, 3.3, 3.4, 3.5, 3.8, 3.9, 3.11, 3.12, 3.15_

- [ ] 7. Checkpoint - validate the policy, atomic repository, dispatch, and state-machine core
  - Run one-shot focused suites for policy/runtime models, both repository implementations and exact Lua, queue/dispatcher, `WatchService`, `monitor_watch`, Celery config, and sibling hardening behavior.
  - Run deterministic model traces and barrier-based concurrency tests with bounded operation counts; confirm task 1 defect tests now pass for completed phases and task 2 preservation tests remain unchanged/pass.
  - Run `python -m compileall backend tests` and `git diff --check`; resolve failures before shared mock/recovery work and do not start Redis, Celery, an API server, a watcher, or any interactive process.
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.11, 2.15, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.11, 3.12, 3.13, 3.15_

- [ ] 8. Add shared atomic mock booking state and bounded capacity cleanup

  - [ ] 8.1 Define `MockBookingStateRepository` and equivalent in-memory behavior
    - Create `backend/db/repositories/mock_booking.py` with atomic publish/filter, operation pin/release, get/reconcile booking, book slot, and bounded cleanup decisions using an injected clock.
    - Implement one in-memory repository lock shared by all adapters/services in a process. Atomically publish deterministic candidates, filter active tombstones, touch admitted slots, and enforce configurable unbooked capacity/idle TTL.
    - Evict oldest eligible idle unbooked/unpinned slots; never evict an in-flight pin, unexpired confirmation, tombstone, or idempotency record. If all state is protected or a query exceeds capacity, deterministically omit/truncate new candidates instead of exceeding capacity.
    - Store confirmation/tombstone/idempotency protection under one `protected_until` of at least seven days and remove related records together only after expiry.
    - _Bug_Condition: adapter-local dictionaries and unsynchronized search writes allow divergent bookings, replay misses, resurrection, and unbounded slots_
    - _Expected_Behavior: one repository provides atomic booking/idempotency and bounded publication for every in-process adapter_
    - _Preservation: deterministic slot identity/capacity/availability and explicit `MOCK_CONFIRMED` results remain unchanged_
    - _Requirements: 2.7, 2.8, 2.11, 3.8, 3.9, 3.13_

  - [ ] 8.2 Implement exact Redis mock-state scripts and retention indexes
    - Add production Lua for publish/admit/filter/evict/pin, cross-key booking, reconciliation, and coordinated cleanup over hashed `dibs:mock:` keys and bounded LRU/expiry indexes.
    - Booking must return existing confirmation for the same key, reject another key for a protected booked slot, reject missing generated slots, atomically create deterministic confirmation/tombstone/idempotency records, remove the unbooked slot, and apply native TTLs only as cleanup-grace backstops.
    - Execute the exact script sources/codecs in the same implementation-verified Lua harness chosen in task 4; do not add another dependency or use a hand-written fake as atomicity evidence.
    - Compare bounded generated traces and concurrent distinct-key races with the in-memory implementation, including capacity one, all pinned, oversized query, exact seven-day boundaries, crash-expired pins, and cleanup.
    - _Bug_Condition: API/worker processes cannot share atomic slot/booking/idempotency state and cleanup can break replay protection_
    - _Expected_Behavior: Redis and memory implement the same protected booking and bounded-capacity decisions_
    - _Preservation: slot IDs, mock confirmations, and stable idempotency semantics remain provider-compatible_
    - _Requirements: 2.7, 2.8, 2.11, 3.8, 3.9, 3.13_

  - [ ] 8.3 Make `MockBookingAdapter` stateless and rebind every service to the selected repository
    - Keep deterministic slot generation in `backend/integrations/mock_booking.py`, but delegate publication, searches, bookings, replay, and reconciliation to the injected state repository; release operation pins in bounded `finally` paths.
    - Update `BookingService`, `WatchService`, API lifespan wiring, and every worker child to use adapter instances over the same selected repository. Redis/Celery uses the common client/prefix; fallback creates exactly one in-memory mock repository per process and shares it across services.
    - Preserve provider-neutral adapter interfaces for non-mock providers and explicitly document that shared demo idempotency is not a real-provider guarantee.
    - _Bug_Condition: independently constructed API/worker adapters own isolated mutable state_
    - _Expected_Behavior: all adapters in the selected topology observe one injectable repository and authoritative reconciliation_
    - _Preservation: booking API/orchestrator behavior, deterministic searches, fallback without infrastructure, and `watch:{watch_id}` remain unchanged_
    - _Requirements: 2.7, 2.8, 2.11, 3.6, 3.8, 3.9_

  - [ ] 8.4 Re-run the same shared-state characterization and preservation tests
    - **Property 1: Expected Behavior** - Re-run task 1 cross-adapter booking, replay, later search, concurrent publication, capacity, pin, and retention counterexamples against memory and exact Lua.
    - **Property 2: Preservation** - Re-run task 2 deterministic slot identity/availability/capacity, confirmation status, booking key, booking service, and fallback assertions unchanged.
    - Use one-shot focused pytest commands only. **EXPECTED OUTCOME**: shared atomic/bounded cases pass and preserved mock/API behavior remains green.
    - _Requirements: 2.7, 2.8, 2.11, 3.6, 3.8, 3.9, 3.13_

  - _Requirements: 2.7, 2.8, 2.11, 3.6, 3.8, 3.9, 3.13_

- [ ] 9. Add terminal TTL, bounded cleanup, and index self-healing

  - [ ] 9.1 Make terminal transitions and retention atomic
    - On every terminal transition, atomically clear `next_check_at`, active/schedule/claim/dispatch state, add the terminal cleanup deadline, remove active membership, and retain document/runtime/fence/event/all-index visibility through the configured seven-day-default window.
    - Apply matching absolute expiries as backstops while keeping active documents free of TTL; never let a tombstone/event sidecar expire before its protected record.
    - Keep terminal watches retrievable/listed through the full retention boundary and never return them as active.
    - _Bug_Condition: terminal data/all-index membership persists forever or active state can be removed too early_
    - _Expected_Behavior: terminal data has one configurable bounded retention lifecycle while active data remains durable_
    - _Preservation: terminal status/body remains retrievable and exact during its documented retention window_
    - _Requirements: 2.13, 2.5, 3.2, 3.3_

  - [ ] 9.2 Self-heal indexes and run bounded coordinated cleanup
    - Make `get`, list, save/migration, cleanup, and recovery prune missing, corrupt, identity-mismatched, terminal-in-active, or otherwise stale members immediately without returning them as active.
    - Process due terminal and mock cleanup indexes in configurable bounded batches, revalidate state/deadline before deletion, and remove document/sidecars plus all/active/schedule/terminal membership atomically.
    - Treat cleanup failure as delayed—not shortened—retention; leave due work discoverable, report backlog/failure, and never use unbounded scans or `KEYS`.
    - _Bug_Condition: native TTL cannot remove set members and skipped corrupt/missing records leave stale indexes indefinitely_
    - _Expected_Behavior: every read/cleanup/recovery path repairs stale membership and bounded sweeps eventually remove due terminal state_
    - _Preservation: valid list/get order/content and active durability remain unchanged_
    - _Requirements: 2.8, 2.9, 2.10, 2.13, 3.2, 3.13_

  - [ ] 9.3 Re-run the same retention/index characterization and preservation tests
    - **Property 1: Expected Behavior** - Re-run task 1 TTL boundary, stale/corrupt index, active durability, cleanup failure/backlog, and terminal retrieval counterexamples against both repositories.
    - **Property 2: Preservation** - Re-run task 2 valid list/get, cancellation, terminal status/outcome, and no-live-service assertions unchanged.
    - Use one-shot focused pytest commands only. **EXPECTED OUTCOME**: retention/self-healing properties pass without shortening valid records or changing public results.
    - _Requirements: 2.8, 2.9, 2.10, 2.11, 2.13, 3.2, 3.3, 3.13_

  - _Requirements: 2.5, 2.8, 2.9, 2.10, 2.11, 2.13, 3.2, 3.3, 3.13_

- [ ] 10. Add coordinated startup recovery and lifespan ownership

  - [ ] 10.1 Implement `RecoveryCoordinator` in `backend/services/watch_recovery.py`
    - Inject repository, dispatcher, UTC clock, owner ID, a small readiness-observation sink protocol, wake event, and bounded batch settings; use a fake sink in coordinator tests and defer the concrete shared `ReadinessTracker`/health projection to task 11. Do not read global infrastructure in coordinator logic.
    - In Redis mode, acquire/renew/release a finite compare-owner leader lease and stop scanning immediately on renewal loss; retain per-window dispatch leases as the final idempotency boundary. In memory mode, use process-local coordination and report its limited scope without claiming restart durability.
    - Reconcile every available active-index record: prune missing/corrupt/terminal, conditionally expire overdue/exhausted, preserve future absolute due times, dispatch due/overdue markers, defer live claims/dispatch leases, and conditionally synthesize a marker for legacy active records lacking one without scheduling past `expires_at`.
    - Continue after individual failures, record structured redacted fields/retry times, run bounded terminal/mock cleanup, signal/wake on marker changes, and retain degraded readiness while due backlog or failures remain. Another owner must resume after lease expiry.
    - _Bug_Condition: stranded active watches receive no startup repair and replica startups can duplicate or abandon scheduling_
    - _Expected_Behavior: one finite coordinator safely classifies/repairs all visible records and permits takeover after failure_
    - _Preservation: future watches keep their persisted due time and asyncio mode claims only records visible to its selected repository_
    - _Requirements: 2.9, 2.10, 2.11, 2.13, 3.5, 3.6_

  - [ ] 10.2 Wire recovery after final store/queue/mock selection in `backend/main.py`
    - Build memory repository/queue/shared mock state first, perform optional Redis/Celery upgrade and unsupported-cluster checks, then rebind `BookingService`, `WatchService`, dispatcher, and adapters to the final selected components before starting recovery.
    - Refuse the distributed atomic upgrade for detected Redis Cluster/cross-slot topology, record a degraded unsupported reason, and retain documented memory/asyncio fallback; keep `rediss://` and the current direct-primary topology supported.
    - Start the initial finite reconciliation after final binding; schedule bounded follow-up sweeps on the application loop, and shut down in order: recovery coordinator, queue, Redis client, orchestrator/runner, all idempotently.
    - Ensure a first broker publication failure after durable watch creation does not change the preserved 201/body and remains recoverable from the due marker.
    - _Bug_Condition: `_attach_redis` selects infrastructure but performs no reconciliation and can leave services bound to different state owners_
    - _Expected_Behavior: final topology wiring is internally shared and recovery owns every persisted schedule only after selection is complete_
    - _Preservation: Redis/Celery optionality, memory/asyncio fallback, immediate creation response, and lazy cleanup remain intact_
    - _Requirements: 2.7, 2.9, 2.10, 2.11, 2.13, 3.1, 3.6, 3.12_

  - [ ] 10.3 Re-run the same recovery/lifespan characterization and preservation tests
    - **Property 1: Expected Behavior** - Re-run task 1 due/future/far-future/expired/exhausted/missing/corrupt/terminal/live-claim records, two leaders, owner death, dispatch failure, marker repair, and cleanup backlog using injected doubles.
    - **Property 2: Preservation** - Re-run task 2 reachable/unreachable Redis, Celery present/absent, selected store/queue, immediate first dispatch, shared service binding, shutdown, and optional-import assertions unchanged.
    - Use one-shot focused pytest commands only. **EXPECTED OUTCOME**: startup/follow-up recovery passes deterministically and all fallback/lifecycle behavior remains green.
    - _Requirements: 2.7, 2.9, 2.10, 2.11, 2.13, 3.1, 3.5, 3.6, 3.12, 3.13_

  - _Requirements: 2.7, 2.9, 2.10, 2.11, 2.13, 3.1, 3.5, 3.6, 3.12, 3.13_

- [ ] 11. Add evidence-based queue/recovery health reporting

  - [ ] 11.1 Implement `ReadinessTracker` and additive `/health` fields
    - Create `backend/services/readiness.py` to record actual selected store/queue and last performed queue/recovery observations with `ready`, `degraded`, or `unknown`; distinguish configured/importable from probed and keep timestamps/details bounded and redacted.
    - Report `watch_queue` from the final bound queue instance. Asyncio is ready only when open on the running loop; Celery is ready only after a successful finite broker-path probe/dispatch, degraded after a performed failure, and unknown when merely configured/importable. Redis ping alone does not prove broker/worker consumption.
    - Add `queue_readiness` and `recovery_readiness` without changing HTTP 200 or existing `status: ok`, `service: dibs-mvp`, `config`, and actual `watch_store` meanings.
    - _Bug_Condition: health omits actual queue mode and can imply readiness from configuration/import rather than performed evidence_
    - _Expected_Behavior: additive fields truthfully represent selection and observed readiness without redefining top-level health_
    - _Preservation: existing health fields/status code and optional-infrastructure operation remain unchanged_
    - _Requirements: 2.10, 2.14, 3.6, 3.10_

  - [ ] 11.2 Feed dispatcher/recovery outcomes into readiness and structured diagnostics
    - Update readiness after finite broker probes/dispatches, initial/full recovery passes, leader loss, candidate failures, and cleanup backlog; successful later observations may recover degraded state.
    - Log claim result, stale/fenced count, outage/backoff, marker age, recovery pass, stale prune count, mock capacity/evictions, cleanup lag, and selected mode without secrets, booking details, credentials, or raw connection URLs.
    - _Bug_Condition: dispatch/recovery failures are invisible or health claims more than the application verified_
    - _Expected_Behavior: readiness and diagnostics expose bounded actionable degradation while retries remain recoverable_
    - _Preservation: top-level health remains `ok` and logs do not alter worker retry/outcome contracts_
    - _Requirements: 2.10, 2.14, 3.10, 3.11, 3.15_

  - [ ] 11.3 Re-run the same health characterization and preservation tests
    - **Property 1: Expected Behavior** - Re-run task 1 store/queue selections and all performed/not-performed probe histories; assert exact `ready`/`degraded`/`unknown` derivation.
    - **Property 2: Preservation** - Re-run task 2 HTTP success and existing `status`, `service`, `config`, and `watch_store` assertions unchanged.
    - Use one-shot focused pytest commands only. **EXPECTED OUTCOME**: additive readiness tests pass and existing health clients remain compatible.
    - _Requirements: 2.10, 2.11, 2.14, 3.6, 3.10_

  - _Requirements: 2.10, 2.11, 2.14, 3.6, 3.10, 3.11, 3.15_

- [ ] 12. Checkpoint - validate shared state, retention, recovery, and readiness
  - Run one-shot focused mock-state tests against memory/exact Lua, retention/index model tests, recovery leader/dispatch concurrency tests, lifespan integration tests, and health selection/probe-history tests.
  - Re-run every task 1 characterization group completed through this phase and the entire unchanged task 2 preservation suite, including `watch-route-worker-retry-hardening`; resolve failures before deployment assets.
  - Run `python -m compileall backend tests` and `git diff --check`; do not start Redis, Celery, an API server, a watcher, or any interactive/long-running process.
  - _Requirements: 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.13, 2.14, 2.15, 3.1, 3.2, 3.3, 3.6, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13, 3.15_

- [ ] 13. Add unattended container assets and migration/deployment guidance

  - [ ] 13.1 Add a reproducible root `Dockerfile` and `.dockerignore`
    - Use a pinned Python 3.12 base image, install pinned project worker/runtime dependencies, copy only required runtime sources, run as a non-root user, and default to `python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000` without reload.
    - Define a finite stdlib HTTP health check and support overriding the command to `celery -A backend.workers.celery_app worker --loglevel=info`; neither role command may be interactive or watcher-based.
    - Exclude `.git`, `.venv`, caches, coverage, editor files, `.env*`, local Redis/Postgres data, tests, and spec/build artifacts not needed at runtime.
    - _Bug_Condition: repository assets do not define a reproducible unattended API/worker image path_
    - _Expected_Behavior: one non-root image supports finite startup configuration for both roles and an API health check_
    - _Preservation: runtime dependency pins and local host execution remain available_
    - _Requirements: 2.12, 3.13, 3.14_

  - [ ] 13.2 Optionally expose application roles through the existing compose file without replacing infrastructure
    - Add `api` and `worker` services behind an `app` profile only if compose application roles are included; build both from the same image, make API depend on healthy Redis/expose 8000, and give worker the explicit Celery command.
    - Preserve existing Redis/PostgreSQL image tags, ports, commands, volumes, environment, health checks, and default infrastructure-only flow exactly; static tests must compare these definitions.
    - _Bug_Condition: operators lack an optional compose path for unattended roles or an extension damages existing infrastructure behavior_
    - _Expected_Behavior: optional app services are additive and use one version-aligned image_
    - _Preservation: Redis/PostgreSQL data/health behavior and infrastructure-only compose usage remain unchanged_
    - _Requirements: 2.12, 3.14_

  - [ ] 13.3 Document configuration, migration, deployment, rollback, and one-shot operation
    - Update README/environment guidance with all bounded settings, deadline-capable versus attempt-limited behavior, additive creation headers/health fields, process-local fallback limits, dispatch horizon/recovery/retention, mock replay protection, and no claim of real-provider idempotency.
    - Document v1 sidecar migration and optional operator limit-raise procedure without automatic expansion; state that mixed old/new workers are unsafe because old workers can snapshot-save without fencing.
    - Provide the deployment gate: drain/stop old workers, deploy API and worker from the same image/version, run startup migration/recovery, then start consumers; rollback also stops workers first. Include structured recovery/degradation checks.
    - Keep PowerShell-friendly host commands and clearly mark API/worker/compose-up commands as manual long-running operations; automated validation examples must be one-shot and require no live services.
    - _Bug_Condition: operators cannot safely build/run/migrate the coordinated protocol and may attempt an unsafe rolling worker mix_
    - _Expected_Behavior: documentation gives truthful non-interactive image roles and explicit forward/rollback sequencing_
    - _Preservation: existing Windows-friendly local flow and optional infrastructure remain supported_
    - _Requirements: 2.2, 2.3, 2.6, 2.10, 2.12, 2.13, 2.14, 3.6, 3.9, 3.10, 3.14_

  - [ ] 13.4 Add static/container configuration tests and rerun operability preservation
    - Assert Dockerfile non-root user, pinned base/dependencies, finite API command/health check, worker override, and secret/build-noise exclusions; assert compose app services only under the optional profile and byte/semantic preservation of Redis/PostgreSQL service behavior.
    - **Property 1: Expected Behavior** - Re-run task 1 unattended-build-path assertions; the old missing-asset counterexample must now pass.
    - **Property 2: Preservation** - Re-run task 2 compose/Windows-flow snapshots unchanged.
    - Run one-shot static pytest checks; when Docker is installed, optionally run finite `docker build` and `docker compose -f infra/docker-compose.yml config`. Do not run `compose up`, Uvicorn, Celery workers, watchers, or interactive commands.
    - _Requirements: 2.11, 2.12, 3.13, 3.14_

  - _Requirements: 2.2, 2.3, 2.6, 2.10, 2.11, 2.12, 2.13, 2.14, 3.6, 3.9, 3.10, 3.13, 3.14_

- [ ] 14. Run focused, model, concurrency, integration, and full validation

  - [ ] 14.1 Run focused model/property suites with no live services
    - Run `python -m pytest tests/test_config.py tests/test_watch_policy.py tests/test_watch_repository.py tests/test_watch_repository_state_machine.py tests/test_mock_booking.py tests/test_mock_booking_state.py tests/test_watch_operability.py` for settings/policy math, runtime migration/public projection, normal/outage scheduling, both watch repositories plus exact Lua scripts, both mock repositories, retention/cleanup, health derivation, and static container assets.
    - Require bounded deterministic generators to print seeds/traces; rerun the exact failing seed when fixing a failure rather than adding sleeps or widening expected outputs.
    - Confirm every task 1 defect-reproducing assertion now passes and every task 2 observation-first preservation assertion still passes unchanged.
    - _Requirements: 2.1, 2.2, 2.3, 2.7, 2.8, 2.11, 2.12, 2.13, 2.14, 2.15, 3.1, 3.3, 3.4, 3.5, 3.7, 3.8, 3.9, 3.10, 3.13, 3.14_

  - [ ] 14.2 Run bounded concurrency/state-machine suites
    - Run `python -m pytest tests/test_watch_repository_state_machine.py tests/test_watch_dispatcher.py tests/test_watch_recovery.py tests/test_mock_booking_state.py` with barrier/event-driven traces for duplicate claims/deliveries, cancellation before/after permit, confirmed/absent/unknown reconciliation, crash/lease expiry/takeover, stale commits, final-attempt expiry, dispatch uncertainty, terminal event cardinality, cross-adapter booking, pins/capacity, and two recovery leaders.
    - Compare in-memory and exact-Lua implementations to the same oracle; no test may depend on wall-clock sleeps or a live broker/server/provider.
    - _Requirements: 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.13, 2.15, 3.2, 3.3, 3.8, 3.11, 3.12_

  - [ ] 14.3 Run fake-backed integration suites
    - Run `python -m pytest tests/test_main_watch_wiring.py tests/test_monitor_watch.py tests/test_watch_api.py tests/test_watch_service.py tests/test_booking_service.py` to exercise FastAPI lifespan with Redis reachable/unreachable, Celery present/absent, unsupported cluster detection, final shared adapter binding, queue/store selection, immediate durable dispatch, startup/follow-up recovery, readiness, and shutdown.
    - Exercise decorated `monitor_watch.run` for success, recognized retry/retry exhaustion, non-transient propagation, cancellation, old one-argument jobs, duplicate windows, cleanup, and exact result shape while retaining all `watch-route-worker-retry-hardening` tests.
    - Exercise two service/queue instances over one repository and API/worker adapters over exact-script fake Redis for lost dispatch, recovery/live claim, booking races/replay/search exclusion, retention cleanup, and no duplicate terminal side effects.
    - _Requirements: 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.13, 2.14, 2.15, 3.1, 3.2, 3.3, 3.6, 3.8, 3.9, 3.10, 3.11, 3.12, 3.15_

  - [ ] 14.4 Run the complete one-shot validation and review requirement coverage
    - Run `python -m pytest`, `python -m compileall backend tests`, and `git diff --check`; optionally run finite `docker build` and `docker compose -f infra/docker-compose.yml config` only when Docker is available.
    - Confirm collection and API operation also work when the optional worker dependency is absent and worker tests skip safely; confirm no test starts Redis, Celery, a provider, Uvicorn, a watcher, or an interactive process.
    - Review the diff and requirement matrix for every clause 2.1–2.15 and 3.1–3.15, exact public enums/JSON, immediate first check, dependency pins in both manifests only when the exact-Lua harness required them, migration/deployment notes, and no unrelated scope.
    - Resolve all failures without weakening characterization/preservation assertions; ask the user if a requirement conflict or external tooling limitation remains.
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.13, 2.14, 2.15, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13, 3.14, 3.15_

  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.13, 2.14, 2.15, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13, 3.14, 3.15_

- [ ] 15. Final checkpoint - all tests pass and the bugfix is deployment-ready
  - Verify the exact task 1 exploration tests that failed on old behavior now pass on fixed behavior and the exact task 2 preservation tests still pass without rewritten baselines.
  - Verify each implementation phase was completed in order, its same-test rerun passed, all validation commands were one-shot, and no live external service was required.
  - Confirm public `Watch` JSON/statuses/outcomes, immediate first check, normal jitter/deadline behavior, worker retry/resource contracts, and existing Redis/PostgreSQL local flow remain preserved; confirm additive headers/readiness and deployment migration notes match the design.
  - Record any optional Docker smoke check skipped because Docker was unavailable; do not block deterministic fake-backed correctness on optional local tooling.
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.13, 2.14, 2.15, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13, 3.14, 3.15_
