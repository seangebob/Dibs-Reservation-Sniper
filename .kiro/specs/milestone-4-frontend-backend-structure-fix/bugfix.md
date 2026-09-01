# Bugfix Requirements Document

## Introduction

This bugfix expands the previously validated frontend-facing findings from completed Milestone 4 tasks 1–7 into a repository-backed audit of the complete backend. The affected behavioral component families are API routes and dependencies; public contracts and validation models; orchestration, booking, watch lifecycle, history, notification, scheduling, and recovery services; in-memory, Redis, and PostgreSQL persistence; distributed workers and task queues; configuration, startup, shutdown, health, readiness, and logging; provider and mock-booking adapters; migrations, packaging, containers, and deployment composition; and their cross-module tests. No frontend or production implementation is part of this requirements phase.

The confirmed defects and material structural risks can delay or lose durable projection, ownership, terminal notification, and cleanup work; make request behavior disagree with startup health; accept invalid temporal or catalog states; expose credentials or reservation details; perform unbounded recovery work; use an unsafe or inconsistent Redis topology; report misleading health; leak resources; or ship a deployment in which a healthy worker is marked unhealthy and configured history is unreachable. The requirements preserve all earlier findings supported by the repository, avoid duplicating fixes owned by related hardening specs, and distinguish hypotheses that require exploration tests from confirmed failures.

Clauses 1.1–1.32 describe confirmed code-path defects, material structural risks, or missing invariant evidence. Clause 1.33 contains exploration-only hypotheses and does not assert that those failures occur. The paired 2.x clauses and the regression constraints in section 3 are the acceptance criteria. Validation must be deterministic and must not require live PostgreSQL, Redis, Celery, OpenAI, browser, or third-party provider services.

## Bug Analysis

The bug condition `C(X)` is met when a complete-backend scenario encounters a projection, ownership, lifecycle, topology, validation, catalog, privacy, boundedness, retention, migration, deployment, health, or contract condition identified below, or when a material hypothesis lacks the deterministic exploration needed to classify it.

```pascal
FUNCTION isBugCondition(X)
  INPUT: X of type CompleteBackendScenario
  OUTPUT: boolean

  RETURN
    X.distributedOutcomeCanDivergeFromLocalOutcome OR
    X.optionalProjectionCanDelayLoseOrRegressState OR
    X.liveAndHistoricalStateDisagree OR
    X.startupSnapshotAndRequestBehaviorDisagree OR
    X.partialLifecycleFailureCanLeakOrSkipCleanup OR
    X.redisTopologyIsUnsupportedUnknownOrInconsistentlyChecked OR
    X.inputOrPersistedModelViolatesARequiredInvariant OR
    X.catalogCalendarOrTemporalPolicyIsInconsistent OR
    X.secretOrReservationDetailCanEscapeToDiagnostics OR
    X.terminalSideEffectIsLostOrAmbiguous OR
    X.repositoryOrRecoveryWorkIsUnboundedOrLate OR
    X.retentionOrMigrationIntegrityCannotBeProven OR
    X.roleHealthOrDeploymentCompositionIsInvalid OR
    X.crossModuleContractLacksDeterministicEvidence OR
    X.requiresExplorationClassification
END FUNCTION

// Property: Fix Checking
FOR ALL X WHERE isBugCondition(X) DO
  result ← CompleteBackend'(X)
  ASSERT result satisfies the corresponding Expected Behavior clause
END FOR

// Property: Preservation Checking
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT observable(CompleteBackend(X)) = observable(CompleteBackend'(X))
END FOR
```

`CompleteBackend` is the current implementation and `CompleteBackend'` is the corrected implementation. Observable behavior includes HTTP status, headers, and body shapes; validation outcomes; booking and watch states; schedule, retry, recovery, notification, and cleanup outcomes; durable owner/history results; resource lifecycle; health/readiness semantics; sanitized diagnostics; packaged migrations; and role-specific deployment health.

### Current Behavior (Defect)

1.1 WHEN a watch poll executes through the configured distributed worker instead of the API process's local queue THEN the system constructs the worker watch service without the history recorder used by the API process, so poll outcomes do not update durable history and API `history_readiness` can remain healthy while dashboard data is stale.

1.2 WHEN a history projection write is slow, saturated, or blocked after a live watch transition commits THEN the system awaits PostgreSQL on create, poll, and cancel paths; watch creation also waits before dispatching its first poll, so optional projection work delays live behavior.

1.3 WHEN watch creation accepts a valid client identifier but its first history write fails THEN the system discards the only retained owner association; later ownerless poll or cancel writes can create a permanently unowned projection that never appears on that visitor's dashboard.

1.4 WHEN projection writes for the same watch complete out of transition order THEN the system unconditionally overwrites the row, allowing an older active state to replace a newer cancelled, found, booked, or expired state.

1.5 WHEN durable history disagrees with a live watch record that is still retained THEN the owner-scoped listing returns only the projection and does not reconcile by watch identifier, so stale projected data can override the live source of truth.

1.6 WHEN `X-Dibs-Client-Id` is present but malformed THEN the system silently treats it as absent, can report successful creation of an unowned watch, and later returns no owned result instead of giving the client a recoverable validation signal.

1.7 WHEN a valid owner requests their watch list while history is disabled, failed at configured startup, or fails during the read THEN the system either returns the same empty list used for a legitimate zero-watch result or exposes an uncontrolled server failure; read failures do not update the write-only history readiness signal.

1.8 WHEN an owner has watches whose creation and update orders differ, or has more than 100 watches THEN the system orders by most recent update and silently truncates the bare list at the repository default, rather than returning every watch in deterministic most-recently-created order through an explicit collection contract.

1.9 WHEN owner-scoped history is served or a separate frontend client is built against the tasks 1–7 surface THEN the HTTP flow reaches concrete process persistence state directly and exposes repository-shaped collection behavior, while the lifecycle service obtains its projection port from the persistence layer; persistence availability, application behavior, and the frontend HTTP contract are therefore not isolated behind one substitutable boundary.

1.10 WHEN PostgreSQL or frontend-origin support is explicitly configured with an invalid value, PostgreSQL is unreachable, or a startup migration fails THEN the system can log the problem and silently disable the configured capability while startup and health still appear successful, contrary to the active configuration and migration requirements.

1.11 WHEN `FRONTEND_ORIGINS` contains a URL with a query, fragment, user information, malformed port, or another component that is not part of a browser origin THEN the system can accept the value even though it cannot form a reliable exact CORS origin contract.

1.12 WHEN migration execution raises a database exception outside the narrow configuration-error path, a pool has already been created, or bundled migration files are absent from an installed artifact THEN the system can surface inconsistent startup behavior, leave the pool unclosed, or accept an empty migration set without ensuring the required watch-history schema exists.

1.13 WHEN a PostgreSQL connection attempt fails for a DSN containing credentials or sensitive query values THEN the system includes the complete DSN in the generated configuration error and startup log, exposing secrets.

1.14 WHEN the tasks 1–7 test suite runs THEN the system verifies sequential success and immediate exceptions but does not exercise distributed-worker history composition, blocked projection latency, failed-owner recovery, out-of-order writes, live/history reconciliation, read outages, collection pagination, strict origin parsing, production-shaped migration failures and cleanup, packaged migration discovery, secret redaction, or a frontend-consumable contract-drift check.

1.15 WHEN recovery changes an eligible active watch to `EXPIRED` outside the normal poll lifecycle THEN the system commits the terminal live state without applying the history projection and notification behavior used by normal terminal transitions, so a dashboard can remain `ACTIVE` and the terminal event can remain undelivered.

1.16 WHEN startup retained an invalid or unavailable settings result and the process environment later changes THEN a request dependency can parse the environment again instead of using the startup snapshot and retained error, so request behavior can disagree with `/health`, process composition, and the original failure identity.

1.17 WHEN startup fails after one or more database, Redis, queue, recovery, or provider resources have opened, or WHEN one shutdown operation raises THEN lifecycle cleanup is not isolated by rollback and finalization guarantees, so earlier resources can leak and one close failure can prevent later resources from closing.

1.18 WHEN Redis topology inspection raises, returns an unsupported topology, or is evaluated by a distributed worker rather than the API process THEN the system can treat an unknown probe as non-clustered and the worker can omit the API-equivalent topology gate, allowing process roles to disagree or to use atomic operations under an unsupported topology.

1.19 WHEN a distributed worker initializes its Redis-backed service resources without first initializing its serialized task runner THEN worker shutdown returns before closing Redis, leaving the initialized client open.

1.20 WHEN Redis or PostgreSQL configuration contains credentials or sensitive query values and configuration parsing, object representation, connection, or startup logging fails THEN raw connection URLs or settings representations can expose those secrets outside the specific PostgreSQL error path described by 1.13.

1.21 WHEN a direct API caller submits a reversed time window, a watch date beyond the orchestrator's supported horizon, or a same-day preferred time whose minute matches the clock but whose seconds are already past THEN validation paths can accept an internally reversed, policy-inconsistent, or already elapsed request.

1.22 WHEN watch, runtime, or execution-result data is constructed, deserialized, or updated with non-UTC or unordered timestamps, an unsupported runtime schema version, invalid counters, or a status/slot combination that contradicts its meaning THEN the models do not consistently enforce those invariants, and update paths can retain states that ordinary construction should reject.

1.23 WHEN a venue intentionally uses a closed-Sunday value that is also interpreted as “inherit weekday hours,” or WHEN the rolling one-year acceptance horizon reaches beyond the fixed supported holiday years THEN the system can advertise Sunday availability for a closed venue or accept dates for which holiday closures and sellout policy are incomplete.

1.24 WHEN a terminal watch notification is logged through the default notification behavior THEN the system writes reservation date, venue, party size, and watch identity into logs without a demonstrated operational need or sentinel test preventing sensitive-field disclosure.

1.25 WHEN notification raises after a terminal watch transition has committed THEN the caller can receive a failure even though the watch is already terminal, while a redelivery observes the terminal state as a no-op and no explicit delivery state guarantees that the notification is retried or recoverable.

1.26 WHEN the watch index, active recovery set, or mock reconciliation-pin set grows large THEN repository listing and recovery can read whole sets, issue a single unbounded multi-read or sequential per-record calls, and scan expired pins without a limit, creating memory, latency, and event-loop risks not bounded by the nominal per-pass batch size.

1.27 WHEN a recovery pass lasts longer than its leadership lease, a durable schedule marker enters the dispatch horizon between fixed sweeps, or a follow-up sweep raises unexpectedly THEN leadership is not renewed for the pass, work can be dispatched late by up to the configured sweep interval, and recovery readiness need not become degraded after the loop failure.

1.28 WHEN terminal watches, terminal event identifiers, expired reconciliation pins, booking tombstones, or mock cleanup backlog accumulate THEN cleanup does not consistently remove or report every associated record, so hidden state can grow without bound and operational recovery cannot determine whether cleanup is keeping pace.

1.29 WHEN migration discovery returns no files, two migrations reuse a version, or an already applied migration's contents change THEN startup records only a version and does not establish the required ordered migration identity, so missing, duplicate, or altered schema history can go undetected even when startup succeeds.

1.30 WHEN the packaged worker runs with the image's API HTTP healthcheck, or WHEN the documented application profile enables PostgreSQL without passing its URL and healthy dependency to API and worker roles THEN a functioning non-HTTP worker is marked unhealthy and configured durable history cannot operate through the documented deployment composition.

1.31 WHEN configuration, queue, recovery, or history readiness is degraded but the health endpoint still returns HTTP success and deployment checks inspect only that status THEN orchestration can treat a semantically unready role as healthy because liveness and readiness are not evaluated through a role-appropriate semantic contract.

1.32 WHEN the complete backend test suite runs THEN high line coverage and green sequential tests do not prove cross-process composition, lifecycle rollback, worker-only cleanup, topology-probe failure, direct-route policy parity, model and calendar boundaries, privacy redaction, terminal side-effect recovery, bounded scans, lease duration, sweep timing, retention backlog, migration identity, role-specific health, PostgreSQL deployment wiring, or semantic readiness.

1.33 WHEN public paid-provider/watch endpoints face sustained untrusted traffic, Redis cancellation faces sustained compare-and-set contention, or a terminal notifier fails at an uncertain point THEN the repository currently lacks deterministic exploration evidence to establish whether admission control is required, whether contention can be misreported as not-found, and which notification delivery guarantee is necessary; these are hypotheses rather than confirmed runtime failures.

### Expected Behavior (Correct)

2.1 WHEN a watch poll executes through the configured distributed worker instead of the API process's local queue THEN the system SHALL record the resulting watch state through the same passive history contract used in local execution, SHALL make projection failure observable, and SHALL preserve the worker's existing result and retry semantics.

2.2 WHEN a history projection write is slow, saturated, or blocked after a live watch transition commits THEN the system SHALL complete and dispatch the live operation without waiting for PostgreSQL, SHALL use a bounded handoff that cannot grow without limit, and SHALL contain eventual projection failure to logging/readiness without changing the live result.

2.3 WHEN watch creation accepts a valid client identifier but its first history write fails THEN the system SHALL retain the private watch-to-client association independently of that single write and SHALL restore the correct owner on a later successful projection without adding owner data to the public `Watch` JSON.

2.4 WHEN projection writes for the same watch complete out of transition order THEN the system SHALL enforce a monotonic freshness rule derived from authoritative watch state so an older transition cannot replace a newer transition.

2.5 WHEN durable history disagrees with a live watch record that is still retained THEN the system SHALL reconcile records by watch identifier, SHALL return the live record as authoritative, and SHALL still return history-only terminal records after live-store retention cleanup.

2.6 WHEN `X-Dibs-Client-Id` is present but malformed THEN the system SHALL return a stable client-safe validation response or another explicit recoverable signal, SHALL NOT silently create an unowned watch under the malformed identity, and SHALL continue to distinguish malformed input from an omitted identifier.

2.7 WHEN a valid owner requests their watch list while history is disabled, failed at configured startup, or fails during the read THEN the system SHALL distinguish unavailable history from a legitimate empty collection, SHALL return a stable sanitized error contract for unavailable reads, and SHALL ensure readiness does not remain ready after an observed history-path failure.

2.8 WHEN an owner has watches whose creation and update orders differ, or has more watches than one bounded response may contain THEN the system SHALL return deterministic `created_at`-descending order with a stable tie-breaker and SHALL expose pagination or continuation metadata that makes every owned watch reachable exactly once.

2.9 WHEN owner-scoped history is served or a separate frontend client is built against the tasks 1–7 surface THEN the system SHALL route the operation through a substitutable application-level contract, SHALL confine concrete database and process-state details to composition, and SHALL expose documented HTTP request, success, collection, and error schemas that the frontend can consume without importing backend internals or inferring repository behavior.

2.10 WHEN PostgreSQL or frontend-origin support is explicitly configured with an invalid value, PostgreSQL is unreachable, or a startup migration fails THEN the system SHALL fail startup with a clear sanitized configuration error; when those optional capabilities are not configured, the system SHALL retain an explicit supported standalone mode.

2.11 WHEN `FRONTEND_ORIGINS` contains a URL with a query, fragment, user information, malformed port, path other than an optional root slash, or any component outside an HTTP(S) scheme, host, and valid optional port THEN the system SHALL reject it with a clear sanitized configuration error.

2.12 WHEN migration execution raises a database exception outside the configuration-error path, a pool has already been created, or bundled migration files are absent from an installed artifact THEN the system SHALL normalize the failure into the selected clear startup contract, SHALL close created resources exactly once, and SHALL verify that the required ordered migration and watch-history schema are present before serving history-dependent requests.

2.13 WHEN a PostgreSQL connection attempt fails for a DSN containing credentials or sensitive query values THEN the system SHALL redact secrets from exceptions, logs, health data, and HTTP responses while retaining only non-sensitive context needed to diagnose the target database.

2.14 WHEN the tasks 1–7 test suite runs THEN the system SHALL use deterministic fakes and HTTP/worker contract tests to cover distributed composition, non-blocking bounded handoff, owner recovery, monotonic writes, live/history reconciliation, unavailable reads and readiness, pagination and ordering, strict origin parsing, migration failure/cleanup/package discovery, DSN redaction, and drift between the public backend schema and the future frontend API client, without contacting live external services.

2.15 WHEN recovery changes an eligible active watch to `EXPIRED` outside the normal poll lifecycle THEN the system SHALL apply the same passive projection and terminal-notification contract as any other committed expiry, SHALL make failures observable and recoverable, and SHALL prevent duplicate terminal effects under repeated recovery.

2.16 WHEN startup retained an invalid or unavailable settings result and the process environment later changes THEN every request SHALL use the immutable startup settings snapshot or re-raise the retained sanitized startup error, and request behavior, process composition, and health SHALL describe the same configuration state.

2.17 WHEN startup fails after resources have opened, or WHEN any shutdown operation raises THEN the system SHALL close every initialized resource exactly once in a safe order, SHALL continue cleanup after an individual close failure, and SHALL preserve the primary failure with sanitized cleanup diagnostics.

2.18 WHEN Redis topology inspection raises, returns an unsupported topology, or is evaluated by any process role THEN the system SHALL treat unknown or unsupported topology as unavailable for the atomic repository, SHALL apply one equivalent topology policy in API and worker composition, and SHALL expose the resulting readiness or startup failure without silently downgrading the probe result.

2.19 WHEN a distributed worker initializes Redis-backed service resources without initializing its serialized task runner THEN worker shutdown SHALL still close the Redis client exactly once and SHALL remain idempotent across repeated shutdown signals.

2.20 WHEN Redis or PostgreSQL configuration contains credentials or sensitive query values THEN parsing, representations, exceptions, startup logs, health output, and HTTP responses SHALL redact user information, passwords, tokens, and sensitive query values while retaining only safe endpoint context.

2.21 WHEN a direct API caller submits a reversed time window, an out-of-horizon watch date, or a same-day time already past at clock precision THEN the system SHALL reject it through a stable client-safe validation contract and SHALL enforce the same calendar and temporal policy used by orchestrated requests.

2.22 WHEN watch, runtime, or execution-result data is constructed, deserialized, or updated THEN the system SHALL enforce aware UTC timestamps and required temporal ordering, supported schema versions, valid counters and transitions, and status/slot/booking consistency on every path, including reconstructed and updated records.

2.23 WHEN a venue is intentionally closed on Sunday or an accepted date reaches a calendar-policy boundary THEN the system SHALL represent closure independently from inherited hours and SHALL either provide complete closure/sellout data for every accepted date or reject dates outside the supported calendar with a stable validation result.

2.24 WHEN notification behavior emits operational logs THEN the system SHALL omit reservation date, venue, party size, client identity, credentials, and other unnecessary reservation details, SHALL use only approved non-sensitive correlation data, and SHALL have sentinel tests that fail on sensitive-field disclosure.

2.25 WHEN notification raises after a terminal transition commits THEN the system SHALL report the live terminal result truthfully and SHALL retain an explicit, idempotent, observable delivery outcome that can be safely recovered; it SHALL NOT return an ambiguous failure that later redelivery silently converts into an unrecoverable no-op.

2.26 WHEN watch, active-recovery, or reconciliation-pin indexes grow large THEN the system SHALL process them through bounded pages or batches with stable continuation, SHALL bound per-pass reads and memory, and SHALL avoid sequential unbounded per-record work while still making every eligible record reachable.

2.27 WHEN recovery work can approach its lease duration, a marker approaches the dispatch horizon, or a follow-up loop fails THEN the system SHALL retain valid single-leader authority for the whole pass or stop before authority expires, SHALL wake in time to dispatch within the defined horizon tolerance, and SHALL mark readiness degraded until successful recovery resumes.

2.28 WHEN terminal records and mock-booking reconciliation state reach their retention boundary THEN cleanup SHALL remove all associated event, pin, tombstone, and index state in bounded batches, SHALL report each cleanup class and remaining backlog, and SHALL make repeated cleanup idempotent.

2.29 WHEN migrations are discovered or compared with applied history THEN the system SHALL reject a missing or empty required migration set, duplicate or unordered versions, and changed contents for an applied version, SHALL verify required schema availability, and SHALL include the required migration assets in the installed package.

2.30 WHEN API and worker roles run from packaged or documented deployment assets and durable history is configured THEN each role SHALL use an appropriate liveness/readiness check, the worker SHALL NOT depend on an API-only HTTP endpoint, and every history-using role SHALL receive the sanitized PostgreSQL configuration and wait for the required dependency policy.

2.31 WHEN required configuration, queue, recovery, or history capability is semantically unready THEN the deployment health mechanism SHALL distinguish that state from process liveness and SHALL fail readiness based on the documented payload or a dedicated readiness contract rather than HTTP success alone.

2.32 WHEN the complete backend test suite runs THEN deterministic fakes, fake clocks, bounded generated traces, direct model/API calls, static package/container checks, and injected lifecycle failures SHALL cover every confirmed scenario in 1.15–1.31 as well as the earlier scenarios in 1.1–1.14, without requiring a live external service.

2.33 WHEN the exploration-only scenarios in 1.33 are evaluated THEN deterministic admission, contention, throwing-notifier, redelivery, and role-composition tests SHALL first establish a reproducible counterexample and the required public behavior; unproven hypotheses SHALL remain documented as exploration results and SHALL NOT silently change the established no-authentication or retry contracts.

### Unchanged Behavior (Regression Prevention)

3.1 WHEN an existing script, test, or direct API caller creates a watch without `X-Dibs-Client-Id` THEN the system SHALL CONTINUE TO accept the request, create an unowned watch, and preserve its existing status code and public response shape.

3.2 WHEN `POST /api/parse-and-book` returns any existing execution status, including `WATCH_CREATED` THEN the system SHALL CONTINUE TO preserve the existing response fields and meanings, with `WATCH_CREATED` carrying its `watch_id` for public follow-up access.

3.3 WHEN a watch is serialized through a public API THEN the system SHALL CONTINUE TO use the existing public `Watch` fields, `WatchStatus` values, and `WatchPollOutcome` values, with private owner, runtime, delivery, and projection metadata absent from that model.

3.4 WHEN an existing caller uses unscoped `GET /api/watches` or accesses a specific watch through `GET` or `DELETE /api/watches/{watch_id}` THEN the system SHALL CONTINUE TO preserve the existing status and body contracts and SHALL CONTINUE TO avoid treating anonymous ownership as an authorization boundary.

3.5 WHEN watch creation, claiming, polling, cancellation, expiry, dispatch recovery, or fencing runs THEN the system SHALL CONTINUE TO use the Milestone 3 live repository and single-flight protocol as the correctness authority without making PostgreSQL a participant in claims, leases, commits, or retry decisions.

3.6 WHEN a projection write raises immediately after a live transition has committed THEN the system SHALL CONTINUE TO return the live operation's successful result, log the projection failure safely, and avoid rolling back the live transition.

3.7 WHEN PostgreSQL and frontend origins are both intentionally unconfigured THEN the system SHALL CONTINUE TO start and serve the standalone backend, `/api/*`, and `/health` without requiring a frontend or PostgreSQL connection.

3.8 WHEN CORS is intentionally unconfigured or a non-browser caller uses the API THEN the system SHALL CONTINUE TO preserve existing request behavior; when CORS is configured, the system SHALL CONTINUE TO use explicit origins and methods/headers without wildcard origins or credential support.

3.9 WHEN owner-scoped history is available THEN the system SHALL CONTINUE TO isolate lists by opaque client identifier, retain history-only terminal watches after live cleanup, and avoid exposing one client's watches to another identifier.

3.10 WHEN `/health` is requested THEN the system SHALL CONTINUE TO preserve every pre-existing field and the top-level `status` meaning, with history health remaining additive and expressed using the existing ready/degraded/unknown vocabulary; deployment readiness may evaluate those semantics without removing the public health contract.

3.11 WHEN the distributed worker processes successful polls, recoverable Redis or broker failures, non-recoverable failures, or shutdown THEN the system SHALL CONTINUE TO preserve its existing result shape, narrow retry classification and limits, serialized runner access, and idempotent resource cleanup.

3.12 WHEN the frontend is later implemented as a separate application THEN the system SHALL CONTINUE TO keep the FastAPI backend independently deployable and SHALL CONTINUE TO integrate only through public HTTP contracts rather than backend source imports.

3.13 WHEN validation is run for this bugfix THEN the system SHALL CONTINUE TO pass the existing Milestone 1–3 and completed Milestone 4 tests without weakening their assertions and SHALL CONTINUE TO avoid live PostgreSQL, Redis, browser, broker, or external-provider dependencies in automated tests.

3.14 WHEN startup settings are invalid or unavailable, or worker settings/retry classification is evaluated THEN the system SHALL CONTINUE TO preserve the retained startup-error visibility, missing-settings invariant, and explicit Redis/Kombu retry partition owned by the related startup and worker hardening specs.

3.15 WHEN deadline policy, fenced transitions, durable scheduling, shared mock state, outage backoff, recovery, readiness, or terminal retention executes normally THEN the system SHALL CONTINUE TO preserve the established Milestone 3 guarantees; this bugfix SHALL only close uncovered cross-module integration and invariant gaps rather than replace those mechanisms.

3.16 WHEN a direct API request or persisted model satisfies all existing temporal, schema, counter, and result-shape invariants THEN the system SHALL CONTINUE TO accept and serialize it with the same public field names, enum values, and client-visible meanings.

3.17 WHEN booking search or mock booking runs for a supported venue, date, time, party size, and calendar year THEN the system SHALL CONTINUE TO preserve deterministic slot generation, capacity, idempotency, atomic winner selection, holiday behavior, and normalized provider error contracts.

3.18 WHEN in-memory and Redis repositories process the same valid operation trace THEN the system SHALL CONTINUE TO produce equivalent claim, commit, cancellation, marker, revision, terminal-event, retention, and observable watch outcomes.

3.19 WHEN a successful terminal notification is delivered THEN the system SHALL CONTINUE TO avoid duplicate user-visible delivery under task redelivery while keeping notification metadata outside the public watch representation.

3.20 WHEN public parse, booking, and watch endpoints are used under the current Milestone 4 contract THEN the system SHALL CONTINUE TO require no authentication and SHALL CONTINUE TO treat the client identifier as opaque scoping data rather than proof of identity unless a separately approved requirement follows reproducible exploration evidence.

3.21 WHEN external-provider behavior is tested or an optional provider is unavailable THEN the system SHALL CONTINUE TO normalize provider failures into the established safe application contract, separate untrusted prompt content from instructions, and use injected deterministic doubles instead of live paid-provider calls.
