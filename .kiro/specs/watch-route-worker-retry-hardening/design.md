# Watch Route Worker Retry Hardening Bugfix Design

## Overview

This fix makes the watch-route configuration invariant explicit and narrows worker retries to known recoverable infrastructure failures.

For watch creation, `get_watch_service` remains the request boundary that rejects unavailable `WatchSettings`. It must continue raising the retained `ConfigurationError`, which the application maps to HTTP 503, before `create_watch` performs configured-timezone date validation or calls the service. Once the dependency returns, the route may treat `request.app.state.watch_settings` as present and use its configured timezone directly; the unreachable UTC fallback is removed.

For worker polling, `monitor_watch` will replace `except Exception` with a concrete exception tuple containing Redis connectivity/timeouts and Kombu broker operational failures. It will preserve the existing retry call, traceback logging, task retry metadata, lock, lifecycle, and success response. Exceptions outside that tuple—including `RuntimeError`, `TypeError`, Pydantic `ValidationError`, inconsistent-state failures, and the broad built-in `ConnectionError` and `TimeoutError`—will escape immediately with their original identity.

The change is intentionally limited to `backend/api/dependencies.py`, `backend/api/routes/watches.py`, `backend/workers/tasks/monitor_watch.py`, and focused tests. It does not change queue, repository, service, resource-cleanup, or dependency-pin behavior.

## Glossary

- **Bug_Condition (C)**: Either an absent-`WatchSettings` create-watch path that can appear to select UTC, or a non-recoverable worker exception that the original broad handler sends to `self.retry`.
- **Property (P)**: Missing settings are stopped at the dependency boundary, and worker failures are retried if and only if they belong to the explicit recoverable infrastructure set.
- **Preservation**: Configured-timezone validation, successful creation and polling, recognized infrastructure retries, result shape, runner serialization, cleanup, and API operation without Celery must remain unchanged.
- **WatchSettings**: Validated watch configuration retained on `app.state.watch_settings`, including `timezone_name`, Redis URL, poll timing, and maximum poll attempts.
- **Retained ConfigurationError**: The exact startup exception stored in `app.state.watch_settings_error` when `WatchSettings.from_environment()` fails.
- **Recoverable_Infrastructure_Errors (R)**: The tuple `redis.exceptions.ConnectionError`, `redis.exceptions.TimeoutError`, and `kombu.exceptions.OperationalError`.
- **RedisWatchRepository**: The persistence adapter in `backend/db/repositories/watches.py`; Redis command failures propagate to its caller.
- **CeleryTaskQueue**: The dispatch adapter in `backend/workers/queue.py`; `apply_async` broker failures propagate to its caller.
- **monitor_watch**: The bound Celery task in `backend/workers/tasks/monitor_watch.py` that runs one `WatchService.poll_once` coroutine through a process-persistent `asyncio.Runner`.
- **Original function (F)**: The current `monitor_watch` implementation with `except Exception` and the current route with an apparent UTC fallback.
- **Fixed function (F')**: The implementation with an explicit settings invariant and a narrow infrastructure exception handler.

## Bug Details

### Bug Condition

There are two related boundary defects. The route contains a UTC fallback even though its service dependency is responsible for preventing execution when watch settings are unavailable. The worker then treats every exception escaping `poll_once` as a recoverable infrastructure failure, including programming, validation, and state errors.

Let:

- `R = {redis.exceptions.ConnectionError, redis.exceptions.TimeoutError, kombu.exceptions.OperationalError}` under subclass-aware `isinstance` matching.
- `C_route(X)` hold when `X` is a create-watch execution with no `WatchSettings` and the route would otherwise choose a fallback timezone.
- `C_worker(X)` hold when `X` is a monitor execution whose poll raises `E`, `E` is not an instance of any class in `R`, and `F` requests a retry for `E`.
- `C(X) = C_route(X) OR C_worker(X)`.

`redis.exceptions.ConnectionError` and `redis.exceptions.TimeoutError` are Redis-specific exceptions, not the similarly named Python built-ins. `kombu.exceptions.OperationalError` is also exported by Celery as the identical class and is the wrapper used for recoverable broker connection/channel transport failures.

**Formal Specification:**
```
FUNCTION isRecoverableInfrastructureException(error)
  INPUT: error of type Exception
  OUTPUT: boolean

  RETURN error IS INSTANCE OF redis.exceptions.ConnectionError
         OR error IS INSTANCE OF redis.exceptions.TimeoutError
         OR error IS INSTANCE OF kombu.exceptions.OperationalError
END FUNCTION

FUNCTION isBugCondition(input)
  INPUT: input of type BugfixInput
  OUTPUT: boolean

  IF input.kind = CREATE_WATCH_REQUEST THEN
    RETURN input.watchSettings IS NONE
           AND input.routeWouldUseFallbackTimezone = TRUE
  END IF

  IF input.kind = MONITOR_WATCH_FAILURE THEN
    RETURN NOT isRecoverableInfrastructureException(input.error)
           AND input.originalFunctionRequestedRetry = TRUE
  END IF

  RETURN FALSE
END FUNCTION
```

### Examples

- `RESERVATION_TIMEZONE` is invalid, so startup retains a `ConfigurationError` and leaves `watch_settings` absent. A create-watch request must return HTTP 503 from dependency resolution; it must not validate the date using UTC or call `WatchService.create`.
- Application state has neither `watch_settings` nor a retained configuration error because of an internal wiring defect. `get_watch_service` must raise an immediate invariant `RuntimeError`; it must not let the route silently substitute UTC.
- `poll_once` raises `TypeError("bad state")`. The current broad handler logs and requests a delayed retry; the fixed task must raise that same `TypeError` object immediately without calling `self.retry`.
- `poll_once` raises a Pydantic `ValidationError` while processing state. The fixed task must propagate the same validation failure immediately. Existing repository behavior for corrupt documents that `RedisWatchRepository._decode` deliberately treats as missing is not changed; this rule applies to validation failures that escape the service boundary.
- `poll_once` raises the built-in `ConnectionError` or built-in `TimeoutError`. Neither is sufficient evidence of a Redis or broker failure, so both must propagate without retry.
- A Redis command raises `redis.exceptions.TimeoutError`, or `CeleryTaskQueue.apply_async` raises `kombu.exceptions.OperationalError` while the service reschedules. The task must log the traceback and call `self.retry` with that exact exception and `countdown=60`.
- The worker resource-closed guard raises `RuntimeError("watch worker resources are already closed")`. This is a state/lifecycle failure, not infrastructure connectivity, and must propagate without retry.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- The retained watch `ConfigurationError` remains the error raised by `get_watch_service` and mapped by the application handler to HTTP 503.
- Past dates remain HTTP 422 according to the configured `WatchSettings.timezone_name`, not the server timezone or UTC.
- Valid non-past dates continue to create a watch and dispatch its first check immediately.
- Redis connectivity/timeouts and broker operational transport failures continue to use the original exception in `self.retry`, a 60-second countdown, and the task's `max_retries=3`.
- `logger.exception` continues to capture a traceback for retryable failures, but it is not emitted by this task for failures that are allowed to propagate.
- Successful tasks continue to return exactly `watch_id`, string-valued `outcome`, and `retry_in_seconds`.
- `_runner_lock` continues to serialize all `_runner().run(...)` calls and cleanup against task execution.
- `_close_worker_resources` remains lazy and idempotent: it does not construct resources only to close them, closes an initialized Redis client through the runner, and closes the initialized runner once.
- The API continues to import worker-only Celery/Kombu code only through the existing optional worker path and remains usable with the worker extra absent.
- Existing `WatchService`, `RedisWatchRepository`, `CeleryTaskQueue`, adapter-error handling, and corrupt-document handling remain unchanged.

**Scope:**
All behavior outside the two bug conditions must be unaffected. In particular, this includes:
- list, read, and cancel routes using the same watch-service dependency;
- all valid configured timezones and date boundaries;
- successful, unknown-watch, already-finished, rescheduled, found, booked, and expired poll outcomes;
- provider `AdapterError` handling already internal to `WatchService`;
- worker configuration/service-construction failures that already occur before the task's retry `try` block;
- shutdown before resource initialization and repeated shutdown signals;
- API startup and in-process polling when Celery is unavailable.

## Hypothesized Root Cause

Based on the repository paths and installed dependency metadata, the most likely causes are:

1. **Duplicated configuration policy at the route**: `create_watch` defensively selects `"UTC"` when `app.state.watch_settings` is absent, while `get_watch_service` already raises the retained startup error before route execution.
   - This creates dead behavior that obscures the real HTTP contract.
   - Because `app.state` is dynamically typed, the dependency does not currently state what should happen if both settings and the retained error are unexpectedly absent.

2. **Over-broad worker exception boundary**: `monitor_watch` catches `Exception`, logs it, and unconditionally calls `self.retry`.
   - This includes programming (`TypeError`), validation (`ValidationError`), lifecycle/state (`RuntimeError`), and unrelated application failures.
   - The resource-closed guard is inside this `try`, so its deliberate state failure is currently misclassified as retryable.

3. **Two infrastructure paths need explicit classification**: Redis repository operations and Celery queue dispatch both execute inside `WatchService.poll_once`.
   - `RedisWatchRepository` does not translate Redis command failures; redis-py connectivity and timeout exceptions escape directly.
   - `CeleryTaskQueue` does not translate `apply_async` failures; Kombu wraps recoverable broker transport failures as `kombu.exceptions.OperationalError`.
   - Provider `AdapterError` is already handled by `WatchService` and should not be added to the worker tuple.

4. **Exception names can invite accidental overreach**: Python's built-in `ConnectionError` and `TimeoutError` are broader than the repository's known infrastructure boundaries.
   - redis-py 7.4.1 defines its own `ConnectionError` and `TimeoutError` under `RedisError`, not the built-in classes.
   - Celery 5.6.3 exports the same `OperationalError` class as the resolved Kombu 5.6.2 installation. Celery declares Kombu as a worker dependency; Kombu is not independently pinned in project files, so no new API-level dependency or pin change is necessary for this worker-only import.

## Correctness Properties

The expected result predicate applies the appropriate branch of the combined bug condition:

```
FUNCTION expectedBehavior(input, result)
  INPUT: input of type BugfixInput
  INPUT: result of type BugfixObservation
  OUTPUT: boolean

  IF input.kind = CREATE_WATCH_REQUEST AND input.watchSettings IS NONE THEN
    IF input.retainedConfigurationError IS NOT NONE THEN
      RETURN result.routeEntered = FALSE
             AND result.raisedException IS input.retainedConfigurationError
             AND result.httpStatus = 503
             AND result.fallbackTimezoneUsed = FALSE
    END IF

    RETURN result.routeEntered = FALSE
           AND result.raisedException IS INSTANCE OF RuntimeError
           AND result.fallbackTimezoneUsed = FALSE
  END IF

  IF input.kind = MONITOR_WATCH_FAILURE
     AND NOT isRecoverableInfrastructureException(input.error) THEN
    RETURN result.raisedException IS input.error
           AND result.retryCallCount = 0
           AND result.retryFailureLogCount = 0
  END IF

  RETURN result.behaviorEqualsOriginalForNonBugInput = TRUE
END FUNCTION
```

Property 1: Bug Condition - Enforce Configuration and Failure-Class Boundaries

_For any_ input where the bug condition holds (`isBugCondition` returns true), the fixed request or task boundary SHALL satisfy `expectedBehavior`: absent watch settings never select UTC and are stopped before route work, while every non-recognized worker failure propagates with its original identity without retry or retry-only logging.

**Validates: Requirements 2.1, 2.3**

Property 2: Preservation - Existing Watch and Recoverable Infrastructure Behavior

_For any_ input where the bug condition does not hold (`isBugCondition` returns false), the fixed code SHALL produce the same externally observable result as the original code, including configured-timezone date handling, successful creation and task results, original-exception Redis/Kombu retries with traceback logging and existing retry policy, serialized runner access, lazy idempotent cleanup, and API operation without Celery.

**Validates: Requirements 2.2, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7**

## Fix Implementation

### Changes Required

Assuming the root-cause analysis is correct:

**File**: `backend/api/dependencies.py`

**Function**: `get_watch_service`

**Specific Changes**:
1. Import `WatchSettings` alongside `ConfigurationError` and `Settings`.
2. Preserve the current first-priority check that raises the exact retained `watch_settings_error` object.
3. Read `app.state.watch_settings` as `WatchSettings | None`. If it is still absent with no retained error, raise an immediate `RuntimeError` describing the broken application-state invariant.
4. Return the existing service only after the settings invariant is established. Do not construct settings again and do not translate the retained error.

Conceptually:
```
error := app.state.watch_settings_error
IF error IS NOT NONE THEN
  RAISE error
END IF

settings := app.state.watch_settings
IF settings IS NONE THEN
  RAISE RuntimeError("watch settings invariant violated")
END IF

RETURN app.state.watch_service
```

**File**: `backend/api/routes/watches.py`

**Function**: `create_watch`

**Specific Changes**:
1. Remove the `watch_settings is not None else "UTC"` branch.
2. Treat `request.app.state.watch_settings` as `WatchSettings`, documenting that `get_watch_service` established the invariant before FastAPI invokes the route.
3. Continue constructing `ZoneInfo` from `watch_settings.timezone_name` and leave the HTTP 422 comparison and service call unchanged.

**File**: `backend/workers/tasks/monitor_watch.py`

**Function**: `monitor_watch`

**Specific Changes**:
1. Import the infrastructure exceptions with aliases that cannot be confused with built-ins:
```
from kombu.exceptions import OperationalError as BrokerOperationalError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
```
2. Define one module-level tuple:
```
_RECOVERABLE_INFRASTRUCTURE_ERRORS = (
    RedisConnectionError,
    RedisTimeoutError,
    BrokerOperationalError,
)
```
3. Replace only `except Exception as exc` with `except _RECOVERABLE_INFRASTRUCTURE_ERRORS as exc`.
4. Keep the handler body byte-for-byte equivalent in behavior: `logger.exception(...)`, then `raise self.retry(exc=exc, countdown=60) from exc`.
5. Leave `build_watch_service()` outside the `try`, `max_retries=3` on the decorator, `_runner_lock`, `_resources_closed` guard, cleanup handlers, and the success-result dictionary unchanged.

**Dependency and boundary decisions**:
- Do not catch built-in `ConnectionError`, built-in `TimeoutError`, `OSError`, or generic `Exception`.
- Do not add Pydantic, provider, configuration, or state exceptions to the recoverable tuple.
- Do not add exception translation to `RedisWatchRepository` or `CeleryTaskQueue`; their library-specific failures already reach the task.
- Do not modify `pyproject.toml` or `requirements.txt`. Redis and Celery are pinned directly; Kombu is supplied by the worker extra through Celery and is imported only from the worker-only task module.

**Tests**: `tests/test_watch_api.py` and new `tests/test_monitor_watch.py`

**Specific Changes**:
1. Extend API tests with dependency/configuration and configured-timezone cases using FastAPI dependency/service and queue doubles.
2. Add worker-task tests that call the decorated task's `.run(...)` path with a poll-service double, runner double, and retry spy. Do not start Celery, Redis, a broker, or a development server.
3. Keep worker-module tests conditional on the worker extra so the API/test installation without Celery can still collect and run non-worker tests.

## Testing Strategy

### Validation Approach

Testing follows two phases. First, execute focused tests against the unfixed code to surface the latent route invariant gap and broad retry counterexamples. Then apply the fix and run the same tests plus preservation coverage. All worker cases use direct task/run, poll-service, runner, retry, Redis-client, and queue doubles; no live infrastructure is involved.

### Exploratory Bug Condition Checking

**Goal**: Demonstrate the two bug conditions before implementation and confirm that the repository and queue allow their library exceptions to reach the task unchanged.

**Test Plan**: Invoke `get_watch_service` with controlled `app.state` and invoke `monitor_watch.run(watch_id)` after replacing `build_watch_service`, `_runner`, and `self.retry` behavior with doubles. Run the new expectations on unfixed code before changing the handler.

**Test Cases**:
1. **Missing Settings Invariant Gap**: Set both `watch_settings` and `watch_settings_error` to `None`; assert the dependency raises an invariant `RuntimeError` instead of allowing route execution. This fails on unfixed code because the dependency returns the service.
2. **Programming Failure Counterexample**: A service double raises one `TypeError` instance; assert that exact object escapes and the retry spy has no calls. This fails on unfixed code.
3. **Validation Failure Counterexample**: A service double raises a captured Pydantic `ValidationError`; assert identity-preserving propagation and no retry. This fails on unfixed code.
4. **State Failure Counterexample**: Set `_resources_closed=True`; assert the existing `RuntimeError` escapes without retry or retry-failure logging. This fails on unfixed code.
5. **Broad Built-in Edge Cases**: Raise built-in `ConnectionError` and built-in `TimeoutError`; assert neither is mistaken for its Redis namesake. These fail on unfixed code.
6. **Infrastructure Classification Control**: Raise Redis connection/timeout and Kombu operational errors from the service double; observe that the existing code does retry them, confirming the desired preservation path.

**Expected Counterexamples**:
- The inconsistent dependency state reaches the route-capable service instead of failing its invariant.
- `self.retry` receives programming, validation, state, and broad built-in failures.
- `logger.exception` records those non-retryable failures as though they were transient infrastructure outages.

If Redis or Kombu exceptions do not reach the wrapper unchanged in a queue/repository double test, the root-cause assumption must be revisited before broadening the tuple. No broad built-in catch should be introduced as a shortcut.

### Fix Checking

**Goal**: Verify that every input in the bug condition receives the correct behavior.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  result := executeFixedBoundaryWithDoubles(input)
  ASSERT expectedBehavior(input, result)
END FOR
```

**Checks**:
- For absent settings with a retained error, assert exact exception identity at dependency level, HTTP 503 at API level, and zero service calls.
- For absent settings without a retained error, assert immediate invariant failure and no route fallback.
- For each generated exception outside `R`, assert the same object escapes, `self.retry` has zero calls, and no `monitor_watch failed` traceback log is emitted by the task.

### Preservation Checking

**Goal**: Verify that all behavior outside the bug condition remains equivalent to the original implementation.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT observe(F(input)) = observe(F'(input))
END FOR
```

**Testing Approach**: Capture baseline behavior on unfixed code with deterministic doubles, then run the same assertions after the fix. The exception partition is finite and explicit, so exhaustive pytest parameterization over its members is preferable to random live-infrastructure failures.

**Test Cases**:
1. **Retained Configuration Error**: Make startup/dependency state retain a sentinel `ConfigurationError`; assert the same object is raised and the API response remains 503 before date validation or service creation.
2. **Configured-Timezone Past Date**: Use valid non-UTC `WatchSettings` and a date before the current date in that timezone; assert HTTP 422 and no service call.
3. **Configured-Timezone Valid Date**: Use today or a future date in the configured timezone with an in-memory service and `RecordingTaskQueue`; assert HTTP 201 and one zero-delay dispatch.
4. **Recoverable Retry Contract**: Parameterize over `RedisConnectionError`, `RedisTimeoutError`, and `BrokerOperationalError`; assert the retry spy receives `exc is original` and `countdown == 60`, `monitor_watch.max_retries == 3`, and `caplog` contains traceback-backed `logger.exception` output.
5. **Success Shape**: Return representative `WatchPollResult` values from the runner/service double; assert exact keys `{watch_id, outcome, retry_in_seconds}`, string `outcome`, no retry, and no failure log.
6. **Runner Serialization**: Run two direct task calls in threads through a runner double that records active entries; assert its maximum active count is one.
7. **Closed Resource Behavior**: Assert the resource-closed guard prevents runner execution and now propagates as the intended non-retryable state failure.
8. **Lazy Idempotent Cleanup**: With cache-aware runner and Redis-client doubles, assert no construction when uninitialized, `aclose` before `runner.close` when initialized, and no second close on repeated cleanup.
9. **Optional Celery**: Simulate `ModuleNotFoundError` for the lazy worker task import while Redis and queue behavior use doubles; assert application startup and watch operation continue with the in-process queue. Worker-specific tests use `pytest.importorskip` when the worker extra is absent.

### Unit Tests

- Test `get_watch_service` with present settings, a retained configuration error, and the inconsistent neither-settings-nor-error state.
- Test configured-timezone past and non-past route decisions with a service call double.
- Test each member of `_RECOVERABLE_INFRASTRUCTURE_ERRORS` for logging, original exception identity, retry countdown, and task retry metadata.
- Test `RuntimeError`, `TypeError`, Pydantic `ValidationError`, built-in `ConnectionError`, built-in `TimeoutError`, and a custom exception for immediate identity-preserving propagation.
- Test successful result mapping and the closed-resource guard through task/run doubles.
- Test resource cleanup with cache-aware runner and Redis-client doubles.

### Property-Based Tests

- Generate create-watch state combinations over `{settings present, settings absent}` × `{retained error present, retained error absent}` and verify that only the valid settings state enters route logic, while the retained-error state preserves exact identity and HTTP 503.
- Generate exception instances from the explicit recoverable class set and verify every instance retries exactly once per task invocation with itself as `exc`, countdown 60, traceback logging, and task `max_retries=3`.
- Generate exceptions outside the recoverable set—including custom subclasses of `RuntimeError`, `TypeError`, Pydantic validation failures, and built-in connection/timeout errors—and verify immediate identity-preserving propagation with no retry call or retry-only log.
- Generate successful outcome/retry-delay pairs accepted by `WatchPollResult` and verify the task's exact result shape.
- Implement the finite exception partitions as deterministic parameterized generators with fixed seeds/data. This provides exhaustive boundary coverage without adding a property-testing dependency or contacting live infrastructure.

### Integration Tests

- Exercise the FastAPI create-watch endpoint with unavailable settings and assert dependency-level HTTP 503 occurs before the service double or timezone date branch.
- Exercise past and valid dates under a configured non-UTC timezone using an in-memory repository and recording queue; assert HTTP 422 or normal creation/dispatch respectively.
- Exercise the decorated Celery task's synchronous `.run(...)` entry point with service and runner doubles for one retryable failure, one non-retryable failure, and one success; assert the complete wrapper contract without a worker or broker.
- Exercise concurrent direct task calls and worker cleanup doubles to preserve lock and shutdown behavior.
- Exercise application wiring with the worker import unavailable and assert the API remains operational on the in-process queue.
