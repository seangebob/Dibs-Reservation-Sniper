# Implementation Plan

- [ ] 1. Write bug-condition exploration tests before changing production code
  - **Property 1: Bug Condition** - Enforce Configuration and Failure-Class Boundaries
  - **CRITICAL**: These tests MUST FAIL on the unfixed code; do not change the assertions or production code to make the initial run pass.
  - Add focused dependency/API cases to `tests/test_watch_api.py` using app-state and service doubles. Cover `C_route(X)`: `watch_settings is None` and no retained error must raise an invariant `RuntimeError` before route work, timezone fallback, date validation, service creation, or queue dispatch.
  - Add `tests/test_monitor_watch.py`; guard worker-only imports with `pytest.importorskip` before importing the task module so an installation without the worker extra can still collect and run non-worker tests.
  - Exercise the decorated task's synchronous `.run(...)` path with poll-service, persistent-runner, retry, and logger doubles; do not start Celery, Redis, a broker, or any long-running process.
  - Implement a deterministic, parameterized property over `C_worker(X)`: raise the same captured instance of `RuntimeError`, `TypeError`, Pydantic `ValidationError`, built-in `ConnectionError`, built-in `TimeoutError`, and a custom exception, where none is an instance of the recoverable Redis/Kombu tuple.
  - For every generated non-recoverable exception, assert `expectedBehavior`: the original object escapes by identity, `self.retry` is never called, and the task emits no `monitor_watch failed` retry-failure log.
  - Cover the concrete `_resources_closed=True` counterexample and assert its existing invariant `RuntimeError` escapes without runner execution, retry, or retry-failure logging.
  - Run the focused tests on the unfixed code, confirm the invariant-gap and broad-handler cases fail, and record the concrete counterexamples in the test/task notes before implementing the fix.
  - _Requirements: 2.1, 2.3_

- [ ] 2. Write preservation property tests before changing production code
  - **Property 2: Preservation** - Existing Watch and Recoverable Infrastructure Behavior
  - **IMPORTANT**: Follow the observation-first methodology: run each case against the unfixed code, record the actual baseline contract, encode that contract in deterministic parameterized tests, and verify the tests pass before implementation.
  - In `tests/test_watch_api.py`, cover app-state combinations with dependency, service, repository, and recording-queue doubles: the exact retained `ConfigurationError` object is raised first by `get_watch_service`; the application maps it to HTTP 503 before route/service work; and valid settings return the existing service.
  - Preserve configured-timezone route behavior with a non-UTC `WatchSettings`: a date in the past for that timezone returns HTTP 422 with no service call, while today/future creates normally with HTTP 201 and exactly one zero-delay first dispatch.
  - In `tests/test_monitor_watch.py`, parameterize over aliased `redis.exceptions.ConnectionError`, `redis.exceptions.TimeoutError`, and `kombu.exceptions.OperationalError` instances. Assert the retry spy receives the original object as `exc`, `countdown=60`, exactly one call per invocation, traceback-backed `logger.exception` output, and task metadata `max_retries == 3`.
  - Generate representative successful `WatchPollResult` outcome/delay pairs and assert exact result keys `{watch_id, outcome, retry_in_seconds}`, a string-valued `outcome`, no retry, and no failure log.
  - Add a threaded runner-double test proving `_runner_lock` keeps maximum concurrent `_runner().run(...)` entries at one.
  - Add cache-aware runner and Redis-client cleanup tests proving `_close_worker_resources` constructs nothing when caches are empty, runs initialized Redis `aclose` before runner close, closes the initialized runner once, and remains idempotent across repeated shutdown calls.
  - Preserve optional-worker collection and API operation: place import skips before worker-module imports, simulate the lazy worker import raising `ModuleNotFoundError`, and assert startup/watch operation still uses the in-process queue without importing Celery/Kombu through the API path.
  - Restore patched module globals, LRU caches, logger state, and resource flags after each task-wrapper case so tests remain isolated and order-independent.
  - Run the preservation tests on the unfixed code and confirm they pass before implementing the fix.
  - _Requirements: 2.2, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_

- [ ] 3. Fix watch configuration and worker retry boundaries

  - [ ] 3.1 Enforce the watch-settings invariant in `get_watch_service`
    - Import `WatchSettings` with the existing configuration types in `backend/api/dependencies.py`.
    - Keep the retained `watch_settings_error` check first and raise that exact `ConfigurationError` object without reconstruction or translation.
    - Read `app.state.watch_settings` as `WatchSettings | None`; if it is absent after the retained-error check, raise an immediate invariant `RuntimeError` before returning the existing service.
    - Do not rebuild settings or alter list/read/cancel dependency behavior.
    - _Bug_Condition: `C_route(X)` where `watchSettings is None` and route fallback would otherwise be reachable_
    - _Expected_Behavior: `expectedBehavior` stops route entry, preserves retained-error identity/HTTP 503, or raises an invariant `RuntimeError`, and never uses a fallback timezone_
    - _Preservation: The retained configuration failure remains first-priority and valid configured dependencies return the existing service_
    - _Requirements: 2.1, 3.7_

  - [ ] 3.2 Remove the unreachable UTC branch from `create_watch`
    - In `backend/api/routes/watches.py`, treat `request.app.state.watch_settings` as the `WatchSettings` invariant established by `get_watch_service`.
    - Remove only the `None`/`"UTC"` fallback and construct `ZoneInfo` directly from the configured `timezone_name`.
    - Leave the HTTP 422 past-date comparison and `service.create(..., auto_book=...)` path unchanged.
    - _Bug_Condition: `C_route(X)` where missing settings could appear to select UTC_
    - _Expected_Behavior: No route path substitutes UTC or another timezone when settings are unavailable_
    - _Preservation: Configured-timezone date boundaries, successful creation, and initial dispatch remain unchanged_
    - _Requirements: 2.1, 3.1, 3.2_

  - [ ] 3.3 Restrict `monitor_watch` retries to the explicit infrastructure tuple
    - In `backend/workers/tasks/monitor_watch.py`, import `kombu.exceptions.OperationalError`, `redis.exceptions.ConnectionError`, and `redis.exceptions.TimeoutError` with unambiguous broker/Redis aliases and define one module-level recoverable exception tuple.
    - Replace only the broad `except Exception` clause with the aliased tuple; do not catch Python built-in connection/timeout errors, `OSError`, Pydantic/provider/configuration/state exceptions, custom exceptions, or generic `Exception`.
    - Keep the retry-handler behavior equivalent for recognized failures: `logger.exception` retains traceback context and `self.retry` receives the original exception with `countdown=60` and exception chaining.
    - Keep `build_watch_service()` outside the retry `try`, decorator `max_retries=3`, `_runner_lock`, the resource-closed guard, shutdown/cleanup handlers, and the success dictionary unchanged.
    - Do not translate exceptions in repositories/queues, add dependencies or pins, or modify service/adapter behavior.
    - _Bug_Condition: `C_worker(X)` where the poll raises `E` outside `R = (RedisConnectionError, RedisTimeoutError, BrokerOperationalError)` and the original broad handler requests retry_
    - _Expected_Behavior: Non-members of `R` propagate with original identity, zero retry calls, and zero retry-failure logs; members of `R` retain the existing retry contract_
    - _Preservation: Original-exception Redis/Kombu retry, traceback logging, countdown, retry limit, runner serialization, cleanup, and success result remain unchanged_
    - _Requirements: 2.2, 2.3, 3.3, 3.4, 3.5, 3.6, 3.7_

  - [ ] 3.4 Verify the bug-condition exploration tests now pass
    - **Property 1: Expected Behavior** - Enforce Configuration and Failure-Class Boundaries
    - Re-run the same tests from task 1; do not replace them with new post-fix tests.
    - Confirm missing settings cannot enter route work or use UTC and every generated non-recoverable worker exception escapes by identity without retry or retry-failure logging.
    - **EXPECTED OUTCOME**: The tests that failed on the unfixed code now pass.
    - _Requirements: 2.1, 2.3_

  - [ ] 3.5 Verify all preservation property tests still pass
    - **Property 2: Preservation** - Existing Watch and Recoverable Infrastructure Behavior
    - Re-run the same tests from task 2; do not rewrite their baseline expectations after the fix.
    - Confirm configured-timezone API behavior, exact Redis/Kombu retry semantics, success serialization, runner locking, cleanup, and optional-worker operation are unchanged.
    - **EXPECTED OUTCOME**: All preservation tests continue to pass with no regression.
    - _Requirements: 2.2, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_

- [ ] 4. Checkpoint - validate the completed bugfix and review the diff
  - Run the focused suite once with the worker extra available: `python -m pytest tests/test_watch_api.py tests/test_monitor_watch.py`.
  - Run the complete suite: `python -m pytest`.
  - Run compile/collection diagnostics: `python -m compileall backend tests` and a non-worker environment collection/run confirming worker tests skip safely while API tests remain operational.
  - Resolve all failures and diagnostics without weakening the bug-condition or preservation assertions.
  - Run `git diff --check`, then review the scoped diff for `backend/api/dependencies.py`, `backend/api/routes/watches.py`, `backend/workers/tasks/monitor_watch.py`, `tests/test_watch_api.py`, and `tests/test_monitor_watch.py`.
  - Confirm no dependency files, repository/queue/service implementations, or unrelated behavior changed; ensure all requirements are covered before marking the checkpoint complete, and ask the user if questions arise.
  - _Requirements: 2.1, 2.2, 2.3, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_
