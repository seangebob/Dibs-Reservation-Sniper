# Bugfix Requirements Document

## Introduction

This bugfix corrects the frontend-facing backend seams introduced by completed tasks 1–7 of the active `milestone-4-write-api-and-frontend` spec. No Next.js frontend has been implemented yet; the affected scope is the backend contract that the later frontend tasks will consume: anonymous client identity, watch creation and owner-scoped listing, durable watch projection, API and worker composition, CORS, startup configuration and migrations, health/error reporting, and their automated tests. The current implementation can delay live watch work, lose ownership, serve stale or falsely empty dashboard data, and behave differently in the production worker path than in the tested API-local path. The numbered clauses below are the acceptance criteria for correcting those defects while preserving Milestone 1–3 behavior and the active Milestone 4 public contracts.

## Bug Analysis

The bug condition `C(X)` is met when a frontend/backend boundary scenario within tasks 1–7 encounters a distributed worker outcome, slow/failed/out-of-order history I/O, a failed initial owner projection, disagreement between live and projected state, an unavailable or large owner listing, malformed client identity, explicitly invalid deployment configuration, migration/package failure, or frontend/backend contract drift.

```pascal
FUNCTION isBugCondition(X)
  INPUT: X of type Milestone4BoundaryScenario
  OUTPUT: boolean

  RETURN X.isWithinTasks1Through7 AND (
    X.pollExecutesInDistributedWorker OR
    X.historyWriteIsSlowFailedOrOutOfOrder OR
    X.initialOwnerProjectionFails OR
    X.liveStateDiffersFromHistory OR
    X.ownerHistoryIsUnavailableOrExceedsOnePage OR
    X.clientIdentifierIsMalformed OR
    X.configuredDependencyIsInvalidOrUnreachable OR
    X.migrationIsMissingFailsOrLeaksResources OR
    X.publicContractDrifts
  )
END FUNCTION

// Property: Fix Checking
FOR ALL X WHERE isBugCondition(X) DO
  result ← Milestone4Boundary'(X)
  ASSERT result satisfies the corresponding Expected Behavior clause
END FOR

// Property: Preservation Checking
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT observable(Milestone4Boundary(X)) = observable(Milestone4Boundary'(X))
END FOR
```

`Milestone4Boundary` is the current implementation and `Milestone4Boundary'` is the corrected implementation. Observable behavior includes HTTP status and body shapes, watch state and scheduling outcomes, worker retry behavior, health fields, CORS behavior, and persisted owner/history results.

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

### Unchanged Behavior (Regression Prevention)

3.1 WHEN an existing script, test, or direct API caller creates a watch without `X-Dibs-Client-Id` THEN the system SHALL CONTINUE TO accept the request, create an unowned watch, and preserve its existing status code and public response shape.

3.2 WHEN `POST /api/parse-and-book` returns any existing execution status, including `WATCH_CREATED` THEN the system SHALL CONTINUE TO preserve the existing response fields and meanings, with `WATCH_CREATED` carrying its `watch_id` for public follow-up access.

3.3 WHEN a watch is serialized through a public API THEN the system SHALL CONTINUE TO use the existing public `Watch` fields, `WatchStatus` values, and `WatchPollOutcome` values, with private owner and projection metadata absent from that model.

3.4 WHEN an existing caller uses unscoped `GET /api/watches` or accesses a specific watch through `GET` or `DELETE /api/watches/{watch_id}` THEN the system SHALL CONTINUE TO preserve the existing status and body contracts and SHALL CONTINUE TO avoid treating anonymous ownership as an authorization boundary.

3.5 WHEN watch creation, claiming, polling, cancellation, expiry, dispatch recovery, or fencing runs THEN the system SHALL CONTINUE TO use the Milestone 3 live repository and single-flight protocol as the correctness authority without making PostgreSQL a participant in claims, leases, commits, or retry decisions.

3.6 WHEN a projection write raises immediately after a live transition has committed THEN the system SHALL CONTINUE TO return the live operation's successful result, log the projection failure, and avoid rolling back the live transition.

3.7 WHEN PostgreSQL and frontend origins are both intentionally unconfigured THEN the system SHALL CONTINUE TO start and serve the standalone backend, `/api/*`, and `/health` without requiring a frontend or PostgreSQL connection.

3.8 WHEN CORS is intentionally unconfigured or a non-browser caller uses the API THEN the system SHALL CONTINUE TO preserve existing request behavior; when CORS is configured, the system SHALL CONTINUE TO use explicit origins and methods/headers without wildcard origins or credential support.

3.9 WHEN owner-scoped history is available THEN the system SHALL CONTINUE TO isolate lists by opaque client identifier, retain history-only terminal watches after live cleanup, and avoid exposing one client's watches to another identifier.

3.10 WHEN `/health` is requested THEN the system SHALL CONTINUE TO preserve every pre-existing field and the top-level `status` meaning, with history health remaining additive and expressed using the existing ready/degraded/unknown vocabulary.

3.11 WHEN the distributed worker processes successful polls, recoverable Redis or broker failures, non-recoverable failures, or shutdown THEN the system SHALL CONTINUE TO preserve its existing result shape, retry classification and limits, serialized runner access, and idempotent resource cleanup.

3.12 WHEN the frontend is later implemented as the separate `apps/web` application THEN the system SHALL CONTINUE TO keep the FastAPI backend independently deployable and SHALL CONTINUE TO integrate only through public HTTP contracts rather than backend source imports.

3.13 WHEN validation is run for this bugfix THEN the system SHALL CONTINUE TO pass the existing Milestone 1–3 and completed Milestone 4 tests without weakening their assertions and SHALL CONTINUE TO avoid live PostgreSQL, Redis, browser, broker, or external-provider dependencies in automated tests.
