# Bugfix Requirements Document

## Introduction

This bugfix hardens watch creation and background polling by making the validated watch-configuration invariant explicit and limiting delayed retries to recoverable Redis and broker transport failures. It prevents unreachable timezone behavior from obscuring request semantics and ensures programming, validation, state, and other non-transient worker failures remain immediately visible while preserving successful watch processing and resource lifecycle behavior.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN watch settings are unavailable during a create-watch request THEN the watch-service dependency raises the retained configuration error and the system returns HTTP 503 before route processing begins, making the route's apparent UTC fallback unreachable.
1.2 WHEN a watch poll raises a recoverable Redis infrastructure failure or Celery/broker dispatch transport failure THEN the worker's broad exception handler logs the failure and requests a retry with the original exception after 60 seconds, up to three retries.
1.3 WHEN a watch poll raises an unexpected programming error, a Pydantic or persisted-state validation failure, or another non-transient exception THEN the worker's broad exception handler incorrectly treats the failure as recoverable and delays its visibility by requesting the same retry sequence.

### Expected Behavior (Correct)

2.1 WHEN watch settings are unavailable during a create-watch request THEN the system SHALL surface the retained configuration error as HTTP 503 before date validation or watch creation, without substituting UTC or any other timezone.
2.2 WHEN a watch poll raises an explicitly recognized, recoverable Redis connectivity or timeout failure or Celery/broker dispatch transport failure THEN the worker SHALL log the failure with its traceback and request a retry using the original exception and a 60-second countdown, up to three retries.
2.3 WHEN a watch poll raises an unexpected programming error, a Pydantic or persisted-state validation failure, or any other exception not explicitly recognized as a recoverable infrastructure or transport failure THEN the worker SHALL propagate the original failure immediately without requesting a retry.

### Unchanged Behavior (Regression Prevention)

3.1 WHEN watch settings are available and a requested reservation date is earlier than the current date in the configured timezone THEN the system SHALL CONTINUE TO reject watch creation with HTTP 422.
3.2 WHEN watch settings are available and a valid requested reservation date is not in the past in the configured timezone THEN the system SHALL CONTINUE TO create the watch normally and dispatch its first background check.
3.3 WHEN a retryable worker failure occurs THEN the system SHALL CONTINUE TO enforce the existing maximum of three retries and 60-second retry countdown.
3.4 WHEN worker tasks execute in the same worker process THEN the system SHALL CONTINUE TO serialize access to the persistent asynchronous runner.
3.5 WHEN worker shutdown or interpreter shutdown closes initialized worker resources THEN the system SHALL CONTINUE TO perform idempotent Redis-client and runner cleanup without creating unused resources solely for cleanup.
3.6 WHEN a watch poll completes successfully THEN the system SHALL CONTINUE TO return a result containing `watch_id`, the string-valued `outcome`, and `retry_in_seconds`.
3.7 WHEN the API runs without the optional worker dependency available THEN the system SHALL CONTINUE TO keep worker-only imports optional and preserve the API's non-Celery operation.
