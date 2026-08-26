# App Logging Startup Error Visibility Bugfix Design

## Overview

The backend already creates useful records through module loggers such as `backend.services.notification_service` and `backend.main`, but the default `uvicorn backend.main:app` path does not guarantee that the `backend` logger hierarchy reaches a terminal handler at INFO level. Separately, `lifespan` deliberately catches `WatchSettings` validation failures so startup can continue, but that catch path stores the exception without logging it.

The fix is intentionally narrow. A small logging initializer will install the standard-library fallback configuration only when the `backend` namespace is still in its default, unhandled state. It will not replace handlers, levels, propagation choices, or formatters supplied by Uvicorn or another host. `lifespan` will invoke that initializer before producing startup records and will emit one ERROR from the existing `WatchSettings` exception branch. No settings parsing, exception construction, dependency behavior, health response, Redis selection, or watch lifecycle behavior will change.

## Glossary

- **Bug_Condition (C)**: Either a backend INFO/WARNING record has no usable route to terminal output during application operation, or lifespan catches a `WatchSettings` `ConfigurationError` without an actionable startup ERROR.
- **Property (P)**: The required observable result for a buggy input: the backend record is output once, or one actionable watch-configuration ERROR is output.
- **Preservation**: All logging topology and non-logging application behavior that must remain unchanged except for the required new visibility.
- **Application_Logger**: The `backend` namespace logger whose descendants include `backend.main` and `backend.services.notification_service`.
- **Host_Logging_Configuration**: Handlers, levels, propagation settings, and formatters supplied before lifespan by Uvicorn, a test harness, or an embedding process.
- **Fallback_Configuration**: A standard-library `logging.basicConfig(level=logging.INFO)` call made without `force`, custom handlers, or a custom formatter, and only when the application logging hierarchy is pristine and unhandled.
- **LoggingNotificationService**: The service in `backend/services/notification_service.py` that emits INFO records for `AVAILABILITY_FOUND`, `BOOKED`, and `EXPIRED` watch transitions.
- **WatchSettings**: The environment-backed settings in `backend/config.py` for timezone, Redis, poll interval, poll jitter, and maximum polling attempts.
- **Retained_Error**: The exact `ConfigurationError` object assigned to `app.state.watch_settings_error` and later raised by `get_watch_service`.

## Bug Details

### Bug Condition

The bug has two manifestations. First, a backend module emits an INFO or WARNING record while the `backend` hierarchy has no usable output handler, so lifecycle and fallback activity is not visible during the Uvicorn process. Second, `WatchSettings.from_environment()` raises during lifespan, and the exception is retained for deferred 503 behavior but no startup ERROR tells the operator which setting failed or why.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type ApplicationLoggingInput
  OUTPUT: boolean

  IF input.kind = BACKEND_RECORD THEN
    RETURN input.loggerName STARTS_WITH "backend"
           AND input.level IN {INFO, WARNING}
           AND NOT hasUsableBackendOutput(input.loggingState)
  END IF

  IF input.kind = LIFESPAN_STARTUP THEN
    RETURN input.watchSettingsResult IS ConfigurationError
           AND actionableWatchSettingsErrorCount(input.output) = 0
  END IF

  RETURN false
END FUNCTION
```

**Expected-Behavior Specification:**
```
FUNCTION expectedBehavior(input, result)
  INPUT: input of type ApplicationLoggingInput
  INPUT: result of type ApplicationStartupObservation
  OUTPUT: boolean

  IF input.kind = BACKEND_RECORD THEN
    RETURN terminalOccurrenceCount(result, input.record) = 1
  END IF

  IF input.kind = LIFESPAN_STARTUP THEN
    RETURN actionableWatchSettingsErrorCount(result.output) = 1
           AND result.errorRecord.level = ERROR
           AND result.errorRecord.message NAMES input.affectedSetting
           AND result.errorRecord.message EXPLAINS input.validationReason
  END IF

  RETURN true
END FUNCTION
```

### Examples

- With default Uvicorn logging and no handler reachable from `backend`, `LoggingNotificationService.notify(..., WatchEvent.BOOKED)` creates an INFO record but the terminal shows no watch transition. After the fix, that record appears once.
- When Redis cannot be reached, `_attach_redis` emits `Redis at <url> is unreachable; watches stay in process memory`, but the WARNING can be absent from the terminal. After the fix, it appears once and in-memory fallback remains active.
- With `WATCH_POLL_INTERVAL_SECONDS=fast`, lifespan retains `ConfigurationError("WATCH_POLL_INTERVAL_SECONDS must be an integer")` and starts without an ERROR record. After the fix, startup still completes and one ERROR names `WATCH_POLL_INTERVAL_SECONDS` and the integer requirement.
- With `WATCH_POLL_INTERVAL_SECONDS=30` and `WATCH_POLL_JITTER_SECONDS=30`, startup retains the relationship-validation error but does not announce it. After the fix, one ERROR states that `WATCH_POLL_JITTER_SECONDS` must be smaller than `WATCH_POLL_INTERVAL_SECONDS`.
- As a preservation edge case, when a host has already attached a custom handler and formatter for `backend` records, initialization must add nothing and the host-formatted record must still be delivered only through the host's configured path.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Existing host handler objects, handler levels, logger levels, propagation settings, streams, filters, and formatter objects remain unchanged.
- Uvicorn's `uvicorn.error` and `uvicorn.access` logging configuration remains untouched; the fallback configures only the otherwise-unhandled standard root path used by propagating `backend` records.
- Repeated initialization does not add handlers after the first effective initialization and does not cause one `LogRecord` to traverse multiple newly-created fallback paths.
- Invalid watch configuration still completes lifespan startup, stores the exact caught `ConfigurationError` object, raises that same object from `get_watch_service`, returns the same 503 detail, and leaves health output unchanged.
- Valid watch configuration still applies timezone, Redis URL, poll interval, jitter, and maximum attempts and retains the existing Redis, Celery, and in-memory fallback decisions.
- No new startup ERROR is emitted for valid `WatchSettings`, and non-watch `Settings` validation behavior is outside the change.

**Scope:**
All observations other than the required visibility of backend INFO/WARNING records and the single new watch-validation ERROR must be unaffected. This includes:
- Hosts that configure logging before lifespan starts
- Repeated lifespans or direct repeated calls to logging initialization in one process
- Requests to health, watch, booking, and orchestrator endpoints
- Valid environment settings and existing infrastructure availability outcomes
- The text, type, traceback, and object identity of stored configuration failures

## Hypothesized Root Cause

Based on the current implementation, the most likely issues are:

1. **Application Namespace Has No Default Output Path**: Every affected module correctly uses `logging.getLogger(__name__)`, but the application never configures the `backend` hierarchy.
   - The Python root logger defaults to WARNING and may have no handler suitable for application INFO records.
   - Uvicorn configures its own logger namespaces; those handlers do not automatically become ancestors of `backend.*` loggers.

2. **INFO Records Are Correctly Created but Filtered Before Output**: `LoggingNotificationService` calls `logger.info` with complete event details, so the missing terminal evidence is a logging topology/threshold problem rather than a notification lifecycle problem.

3. **Fallback Warnings Use the Same Unconfigured Hierarchy**: Redis and Celery fallback messages are emitted by `backend.main`, so they fail for the same reason even though the fallback branches themselves execute correctly.

4. **The Watch Validation Catch Path Has No Logging Side Effect**: `lifespan` assigns `None` to `watch_settings` and the caught object to `watch_settings_error`, then immediately continues to general settings validation and resource attachment without calling `logger.error`.

5. **Naive Global Reconfiguration Would Introduce Regressions**: Calling `basicConfig(force=True)`, assigning handlers unconditionally, or configuring both the root and `backend` logger could replace host formatters or make propagating records appear more than once.

## Correctness Properties

Property 1: Bug Condition - Backend INFO and WARNING Visibility

_For any_ INFO or WARNING record emitted by a `backend` namespace logger when no usable host logging path exists and the namespace is otherwise in its default propagating state, the fixed logging initialization SHALL make that record visible through the fallback output exactly once, including notification lifecycle records and Redis/Celery fallback records.

**Validates: Requirements 2.1**

Property 2: Preservation - Host Logging and Initialization Idempotence

_For any_ logging state where the host already provides a handler reachable by backend activity or has explicitly configured the backend namespace, and for any positive number of repeated initialization calls, the fixed initializer SHALL preserve host handler, level, propagation, filter, stream, and formatter identity, SHALL not accumulate fallback handlers, and SHALL deliver each backend record no more times than the host configuration did before the fix.

**Validates: Requirements 3.1, 3.2**

Property 3: Bug Condition - Actionable WatchSettings Startup Error

_For any_ environment where `WatchSettings.from_environment()` raises `ConfigurationError` for `RESERVATION_TIMEZONE`, `REDIS_URL`, `WATCH_POLL_INTERVAL_SECONDS`, `WATCH_POLL_JITTER_SECONDS`, `WATCH_MAX_POLL_ATTEMPTS`, or an interval/jitter relationship, the fixed lifespan SHALL emit exactly one ERROR that identifies the affected setting or relationship and includes the existing validation reason.

**Validates: Requirements 2.2**

Property 4: Preservation - Startup, Error, Health, and Valid Configuration Semantics

_For any_ valid or invalid watch environment, after excluding the intentionally added logging observations, the fixed lifespan SHALL produce the same application state, startup completion, retained exception identity and detail, request-time status/detail, health response, applied settings, repository selection, and queue/fallback behavior as the original lifespan.

**Validates: Requirements 3.3, 3.4**

## Fix Implementation

### Changes Required

Assuming the root cause analysis is correct:

**File**: `backend/logging_config.py` (new, small utility)

**Function**: `configure_application_logging`

**Specific Changes**:
1. **Detect Host Ownership Before Configuring**: Inspect the `backend` namespace and its reachable ancestors before making changes.
   - Return immediately if `backend` already has a reachable handler.
   - Treat an explicitly set `backend` level or `propagate=False` as host ownership even if no handler is currently reachable; do not override an intentional host choice.
   - Keep the check read-only and scoped to logging metadata; do not emit a probe record.

2. **Install Only the Standard Fallback**: For the default `backend` state (`NOTSET`, propagation enabled, and no reachable handler), call `logging.basicConfig(level=logging.INFO)`.
   - Do not pass `force=True`.
   - Do not supply or replace handler and formatter objects.
   - Do not add a second handler directly to `backend`; records should have one propagation path.
   - Rely on `basicConfig`'s atomic no-op when another initializer has already configured the root logger, making a repeated or racing call safe.

3. **Keep Uvicorn Configuration Independent**: Do not modify or attach handlers to `uvicorn.error` or `uvicorn.access`. Under Uvicorn's default namespace-specific configuration, the fallback root handler serves `backend.*`, while Uvicorn's non-propagating loggers retain their existing handlers and formatters.

**File**: `backend/main.py`

**Functions**: `lifespan`

**Specific Changes**:
1. **Initialize Before Startup Activity**: Call `configure_application_logging()` as the first lifespan action, before `WatchSettings` validation and `_attach_redis`, so both validation failures and infrastructure fallback records are visible.

2. **Log the Existing Watch Error Once**: In the existing `except ConfigurationError as exc` branch for `WatchSettings.from_environment()`, retain the current assignments and add one `logger.error` call.
   - Use a stable operator-facing prefix indicating startup watch configuration validation failed and that watch-dependent requests will return 503.
   - Interpolate `str(exc)` so the existing setting name and validation reason remain actionable.
   - Do not use `logger.exception` or `exc_info=True`; a traceback is not required and could obscure the single concise record.
   - Do not add a second log in `Settings.from_environment()` handling, because `Settings` re-validates watch settings and could otherwise duplicate the same failure.

3. **Preserve Control Flow and Identity**: Do not re-raise, wrap, copy, or reconstruct `exc`; continue into the existing startup flow with the exact object stored in `app.state.watch_settings_error`.

**Files**: `tests/test_logging_config.py` (new) and focused extensions to `tests/test_api.py`

**Specific Changes**:
1. Add isolated logging-state tests that snapshot and restore root/backend handlers, levels, and propagation without closing host-owned handlers.
2. Exercise representative `LoggingNotificationService` INFO records and `backend.main` fallback WARNING records through a captured stream.
3. Parameterize malformed watch environment cases and assert one actionable ERROR plus all existing startup/API behavior.
4. Reuse mocked Redis availability in integration tests; do not require a live Redis or a running Uvicorn server.

## Testing Strategy

### Validation Approach

Validation uses two phases. First, exploratory tests run against the unfixed code to prove the missing-output counterexamples and confirm that record creation and exception retention already work. After implementation, the same examples become fix checks, while differential/state assertions ensure logging is the only changed observation.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples before implementing the fix and confirm whether the failure is in logging topology and the lifespan catch path. If records are not created or a different layer consumes the exception, revise the root-cause hypothesis before changing code.

**Test Plan**: Isolate the root and `backend` logging state, emit records through real application loggers, and capture stream/caplog observations. Start a fresh FastAPI app with malformed watch variables and inspect both log records and retained state. Run these tests on the unfixed code first.

**Test Cases**:
1. **Notification Visibility**: Emit `AVAILABILITY_FOUND`, `BOOKED`, and `EXPIRED` through `LoggingNotificationService` with no backend output path; the unfixed code creates records but does not show them at INFO.
2. **Redis Fallback Visibility**: Mock Redis ping failure during lifespan and verify the existing WARNING is not reliably visible under the unconfigured application hierarchy.
3. **Single-Setting Validation Failure**: Set `WATCH_POLL_INTERVAL_SECONDS=fast`; startup succeeds and stores the error, but the unfixed code emits no startup ERROR.
4. **Relationship Validation Failure**: Set jitter equal to interval; startup succeeds and stores the relationship error, but the unfixed code emits no startup ERROR.

**Expected Counterexamples**:
- A representative backend INFO/WARNING reaches zero terminal-capable handlers despite the application branch executing.
- A retained `ConfigurationError` exists after startup while zero actionable ERROR records identify the malformed watch setting.
- If either record is not created at all, the logging-configuration hypothesis is refuted and the producer path must be re-investigated.

### Fix Checking

**Goal**: Verify that every input satisfying a bug condition produces its required visible result.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  result := runWithFixedLoggingAndLifespan(input)
  ASSERT expectedBehavior(input, result)
END FOR
```

Test the finite lifecycle-event and watch-validation branch sets exhaustively. For ordinary backend logger/message combinations, generate representative names below `backend.*`, INFO/WARNING levels, and messages to verify exactly-one output without coupling the test to one producer.

### Preservation Checking

**Goal**: Verify that non-buggy logging inputs and all non-logging observations retain original behavior.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT loggingTopology(original(input)) = loggingTopology(fixed(input))
  ASSERT emittedRecords(original(input)) = emittedRecords(fixed(input))
END FOR

FOR ALL startupInput DO
  ASSERT preservedStateAndResponses(original(startupInput))
         = preservedStateAndResponses(fixed(startupInput))
END FOR
```

**Testing Approach**: Use property-oriented generation/parameterization for handler topologies, repeated call counts, valid settings, and malformed settings. Compare object identity where preservation is stronger than value equality. Logging tests must restore global logging state in `finally`/fixture teardown so one case cannot make another pass accidentally.

**Test Plan**: Record baseline host-handler and application-state observations on unfixed code, then run the same observations after the change. Exclude only the explicitly required fallback output and watch-validation ERROR from differential comparison.

**Test Cases**:
1. **Host Handler Preservation**: Install a root or `backend` handler with a sentinel formatter, level, filter, and stream; assert identities and output remain unchanged after initialization.
2. **Repeated Initialization**: Invoke initialization multiple times and across repeated lifespan entries; assert handler identities/counts stabilize and one emitted record is observed once.
3. **Invalid Settings Preservation**: For each validation branch, assert startup completion, exact retained exception identity through `get_watch_service`, unchanged 503 detail, and unchanged health JSON.
4. **Valid Settings Preservation**: Use valid boundary and representative values; assert schedule, timezone, maximum attempts, Redis URL, repository, and queue mode match existing behavior.
5. **Infrastructure Fallback Preservation**: Mock unavailable Redis and missing optional worker support; assert in-memory/asyncio choices are unchanged while their existing WARNING becomes visible once.

### Unit Tests

- Test `configure_application_logging` with an unhandled default hierarchy: a backend INFO and WARNING each appear once.
- Test a custom host handler/formatter/level remains the same objects and receives the same single record.
- Test repeated initialization does not change handler count or duplicate output.
- Test `LoggingNotificationService` emits visible INFO for all three `WatchEvent` values after fallback initialization.
- Parameterize every `WatchSettings` validation branch and assert exactly one ERROR names the setting/relationship and includes the reason.

### Property-Based Tests

- Generate backend descendant logger names, INFO/WARNING levels, and arbitrary safe messages; after fallback initialization, each emitted record appears exactly once.
- Generate positive initialization counts and host logging layouts (root handler, backend handler, explicit level, propagation disabled); handler/formatter identity and per-record delivery count remain invariant.
- Generate valid watch-setting combinations within configured bounds and malformed values for each finite validation category; valid settings produce no validation ERROR, while invalid settings produce one ERROR and preserve the same retained exception/state projection.
- Prefer parameterized finite-domain generation with the existing pytest stack; do not add a property-testing dependency solely for this small fix unless task execution shows the finite matrices are insufficient.

### Integration Tests

- Start a fresh app with mocked Redis unavailability and verify successful startup, one visible fallback WARNING, memory storage, and unchanged health output.
- Start with malformed watch settings and a valid API key; verify one startup ERROR, successful lifespan entry, the same retained error object from the watch dependency, unchanged watch-endpoint 503 detail, and unchanged health response.
- Start with valid watch settings and mocked Redis success/failure paths; verify values are applied, repository/queue selection is unchanged, and no watch-validation ERROR is emitted.
