# Implementation Plan

- [x] 1. Write bug condition exploration property tests
  - **Property 1: Bug Condition** - Backend Activity and Watch Validation Visibility
  - **CRITICAL**: Write and run these tests against the unfixed code before creating `backend/logging_config.py` or changing `backend/main.py`; the failures must come from missing output, not from importing a module that does not exist yet.
  - Add `tests/test_logging_config.py` with an isolated logging-state fixture that snapshots and restores root, `backend`, and exercised descendant logger handlers, levels, disabled flags, propagation, filters, streams, and formatter objects without closing host-owned handlers.
  - Exercise the existing `lifespan` boundary so the same tests cover the future first-action initializer without directly importing it: for `kind = BACKEND_RECORD`, set the `backend` hierarchy to `NOTSET`, propagation enabled, and no reachable handler; for `kind = LIFESPAN_STARTUP`, supply a `WatchSettings.from_environment()` result that is a `ConfigurationError` and start a fresh app.
  - Use finite-domain pytest parameterization as the project’s property-testing mechanism; do not add a property-testing dependency. Generate representative `backend` descendant names, INFO/WARNING levels, and safe unique messages, and assert `terminalOccurrenceCount(result, record) = 1` for every generated case.
  - Invoke the real `LoggingNotificationService.notify` for `AVAILABILITY_FOUND`, `BOOKED`, and `EXPIRED` with a representative watch and assert each INFO event is terminal-visible exactly once after lifespan starts.
  - Mock Redis ping failure during lifespan and assert the existing `Redis at <url> is unreachable; watches stay in process memory` WARNING is terminal-visible exactly once; use an async fake client and do not contact Redis or run Uvicorn.
  - Parameterize every watch-validation category with the malformed environment value and expected message fragment: unknown `RESERVATION_TIMEZONE`; unsupported `REDIS_URL` scheme; non-integer, non-positive, below-minimum, and above-maximum `WATCH_POLL_INTERVAL_SECONDS`; non-integer and negative `WATCH_POLL_JITTER_SECONDS`; jitter greater than or equal to interval; and non-integer or non-positive `WATCH_MAX_POLL_ATTEMPTS`.
  - For every invalid case assert `actionableWatchSettingsErrorCount(output) = 1`, level is ERROR, and the message contains the affected setting or interval/jitter relationship plus the unchanged validation reason.
  - Run only these tests on the unfixed code. **EXPECTED OUTCOME**: the visibility and actionable-error assertions FAIL, confirming zero terminal occurrences or zero actionable startup ERRORs while the producer branches still execute.
  - Record the concrete failing case IDs and observed counts/messages; do not weaken assertions or change production code as part of this task.
  - _Requirements: 1.1, 1.2, 2.1, 2.2_

- [-] 2. Write preservation property tests before implementing the fix
  - **Property 2: Preservation** - Host Logging, Startup, API, and Configuration Semantics
  - **IMPORTANT**: Follow observation-first methodology: run the unfixed application for all non-bug-condition and application-state cases, record the actual topology/state/output, encode those observations, and confirm the tests pass before changing production code.
  - In `tests/test_logging_config.py`, parameterize host-owned layouts: a root handler, a `backend` handler, a sentinel formatter/filter/stream, an explicit `backend` level, and `backend.propagate = False`. Snapshot object identities and values, enter lifespan, emit one unique record, and assert handler lists, handler/logger levels, propagation, filters, streams, and formatter identities remain unchanged and delivery count matches the unfixed baseline.
  - Exercise one, two, and five sequential lifespan entries in the same process. This existing boundary will invoke initialization after task 3; assert handler identities/counts stabilize and each unique record is delivered no more times than under the observed host configuration, with no accumulated fallback paths or duplicate output.
  - Extend `tests/test_api.py` with the same complete malformed-watch-settings matrix from task 1. For each case, assert lifespan entry completes; `app.state.watch_settings` is `None`; `app.state.watch_settings_error` is the exact caught `ConfigurationError`; direct `get_watch_service` resolution raises that same object; a watch request returns the existing 503 status and exact `{"detail": str(retained_error)}` body; and `/health` retains its exact status, service, config, and `watch_store` output.
  - Add valid boundary and representative settings cases, including jitter zero and valid interval/jitter pairs. Assert no watch-validation ERROR, exact values in `app.state.watch_settings`, and matching `WatchService` schedule, timezone, and maximum-attempt state.
  - Cover mocked Redis-unavailable and Redis-available/optional-worker-unavailable paths without external services. Assert memory versus `RedisWatchRepository` selection, `asyncio` versus Celery queue mode, client closure, service settings, startup continuation, and health `watch_store` remain the observed baseline; exclude only the new required WARNING visibility from differential output comparisons.
  - Confirm non-watch `Settings` errors retain their current behavior and do not acquire the new watch-validation startup ERROR.
  - Run these preservation tests against the unfixed code. **EXPECTED OUTCOME**: all tests PASS and establish the baseline to preserve.
  - _Requirements: 3.1, 3.2, 3.3, 3.4_

- [ ] 3. Fix application logging and startup error visibility

  - [ ] 3.1 Add the guarded application logging initializer
    - Create `backend/logging_config.py` with a small `configure_application_logging()` utility scoped to the `backend` namespace.
    - Read the `backend` logger and reachable ancestors without emitting a probe record. Return without mutation when a reachable handler exists, `backend` has an explicit level, or propagation is disabled.
    - Only for the pristine default hierarchy (`NOTSET`, propagation enabled, no reachable handler), call `logging.basicConfig(level=logging.INFO)` with no `force`, custom handler, or custom formatter arguments; never attach a second handler directly to `backend` and never modify `uvicorn.error` or `uvicorn.access`.
    - Rely on `basicConfig` atomic no-op behavior for repeated or racing calls, and keep imports free of logging side effects.
    - _Bug_Condition: `input.kind = BACKEND_RECORD` and the INFO/WARNING `backend.*` record has no usable output path as defined by `isBugCondition(input)` in the design_
    - _Expected_Behavior: `terminalOccurrenceCount(result, input.record) = 1` from `expectedBehavior(input, result)` in the design_
    - _Preservation: Preserve all host handler, level, propagation, filter, stream, formatter, and Uvicorn logger topology; repeated calls must not add paths or duplicates_
    - _Requirements: 2.1, 3.1, 3.2_

  - [ ] 3.2 Invoke logging initialization first in FastAPI lifespan
    - Import `configure_application_logging` in `backend/main.py` and call it as the first executable action in `lifespan`, before `WatchSettings.from_environment()`, `Settings.from_environment()`, `_attach_redis()`, or any startup log producer.
    - Do not initialize logging at module import or app construction time, and do not reorder any existing settings, resource attachment, yield, or shutdown behavior after the new first call.
    - _Bug_Condition: startup reaches backend validation, notification, or infrastructure logging with the default unhandled `backend` hierarchy_
    - _Expected_Behavior: all affected backend INFO/WARNING and startup validation records have one output path before they are emitted_
    - _Preservation: Keep app creation side-effect free and preserve lifespan startup, resource selection, and shutdown control flow_
    - _Requirements: 2.1, 2.2, 3.1, 3.2, 3.3, 3.4_

  - [ ] 3.3 Emit one actionable ERROR for the retained WatchSettings failure
    - In only the existing `except ConfigurationError as exc` branch around `WatchSettings.from_environment()`, retain the current state assignments and add one `logger.error` call with a stable startup-validation prefix, a note that watch-dependent requests will return 503, and `str(exc)` so the existing setting name/relationship and validation reason remain intact.
    - Do not log the same failure from the later `Settings.from_environment()` branch; do not use `logger.exception`, `exc_info`, re-raise, wrap, copy, or reconstruct the exception.
    - Keep `app.state.watch_settings_error = exc` so dependency resolution raises the identical object and the current 503 detail is unchanged.
    - _Bug_Condition: `input.kind = LIFESPAN_STARTUP`, `WatchSettings.from_environment()` returns a `ConfigurationError`, and actionable startup ERROR count is zero_
    - _Expected_Behavior: exactly one ERROR names the affected setting or relationship and includes the existing validation reason_
    - _Preservation: Startup continues; retained error identity/text/traceback, dependency behavior, 503 response, health output, and non-watch settings handling remain unchanged_
    - _Requirements: 2.2, 3.3_

  - [ ] 3.4 Verify the bug condition exploration tests now pass
    - **Property 1: Expected Behavior** - Backend Activity and Watch Validation Visibility
    - **IMPORTANT**: Re-run the same task 1 tests; do not replace them with new tests or relax occurrence-count, level, setting-name, or reason assertions.
    - Confirm generated backend INFO/WARNING records, all three notification events, and the Redis fallback WARNING each appear exactly once.
    - Confirm every malformed WatchSettings category emits exactly one actionable ERROR.
    - **EXPECTED OUTCOME**: all task 1 tests PASS, converting each documented counterexample into a fix check.
    - _Requirements: 2.1, 2.2_

  - [ ] 3.5 Verify all preservation tests still pass
    - **Property 2: Preservation** - Host Logging, Startup, API, and Configuration Semantics
    - **IMPORTANT**: Re-run the same task 2 tests; do not add exclusions beyond the explicitly required fallback output and single watch-validation ERROR.
    - Confirm host object identities/topology, repeated initialization counts, startup continuation, retained exception identity, 503 detail, health JSON, valid settings, repository/queue selection, and Redis fallback state match the unfixed baseline.
    - **EXPECTED OUTCOME**: all task 2 tests PASS with no duplicate records or application regressions.
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

- [ ] 4. Checkpoint - Run focused checks, full suite, diagnostics, and diff review
  - Run the focused logging/startup tests first: `python -m pytest tests/test_logging_config.py tests/test_api.py`.
  - Run the configuration, watch API, notification/watch service, repository, and queue regression set: `python -m pytest tests/test_config.py tests/test_watch_api.py tests/test_watch_service.py tests/test_watch_repository.py tests/test_task_queue.py`.
  - Run the full suite once, without watch mode or external services: `python -m pytest`.
  - Run syntax/import diagnostics with `python -m compileall backend tests` and resolve all diagnostics in `backend/logging_config.py`, `backend/main.py`, `tests/test_logging_config.py`, and `tests/test_api.py`.
  - Run `git diff --check`, inspect `git status --short`, and review the focused diff for the new initializer, lifespan changes, tests, and this spec. Confirm there are no changes to settings parsing, exception construction, dependency/health response logic, repository/queue decisions, Uvicorn logger configuration, or unrelated files.
  - Ensure every exploration counterexample is now passing, every preservation baseline remains passing, and no test leaves global logging state or environment variables behind; ask the user if any unresolved question arises.
