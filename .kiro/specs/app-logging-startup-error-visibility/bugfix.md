# Bugfix Requirements Document

## Introduction

The backend currently provides insufficient terminal visibility during Uvicorn demos and operator startup. Existing application INFO and WARNING activity can be hidden when the host has not configured logging for backend modules, and invalid watch environment settings are retained for deferred error handling without an actionable startup record. This bugfix makes those conditions visible while preserving host-owned logging, application availability, and existing configuration-error behavior.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN the backend runs under Uvicorn without a usable host-provided logging configuration for backend modules THEN the system does not reliably show backend INFO and WARNING activity in the terminal, including LoggingNotificationService availability-found, BOOKED, and EXPIRED events and Redis fallback warnings.

1.2 WHEN WatchSettings.from_environment() rejects RESERVATION_TIMEZONE, REDIS_URL, WATCH_POLL_INTERVAL_SECONDS, WATCH_POLL_JITTER_SECONDS, WATCH_MAX_POLL_ATTEMPTS, or a relationship between those settings during FastAPI lifespan startup THEN the system stores the ConfigurationError for later handling but emits no actionable startup error identifying the malformed setting and validation failure.

### Expected Behavior (Correct)

2.1 WHEN the backend runs under Uvicorn without a usable host-provided logging configuration for backend modules THEN the system SHALL make backend INFO and WARNING activity visible once in the terminal, including LoggingNotificationService availability-found, BOOKED, and EXPIRED events and Redis fallback warnings.

2.2 WHEN WatchSettings.from_environment() rejects RESERVATION_TIMEZONE, REDIS_URL, WATCH_POLL_INTERVAL_SECONDS, WATCH_POLL_JITTER_SECONDS, WATCH_MAX_POLL_ATTEMPTS, or a relationship between those settings during FastAPI lifespan startup THEN the system SHALL emit one actionable startup ERROR record that identifies the affected watch setting and the validation reason.

### Unchanged Behavior (Regression Prevention)

3.1 WHEN the host environment already provides logging handlers, levels, or formatting for backend activity THEN the system SHALL CONTINUE TO honor that host-provided logging behavior without unnecessarily replacing it or producing duplicate messages.

3.2 WHEN application logging initialization occurs more than once in the same process THEN the system SHALL CONTINUE TO avoid accumulating handlers or emitting a single backend record multiple times.

3.3 WHEN watch configuration is invalid during FastAPI lifespan startup THEN the system SHALL CONTINUE TO finish application startup, retain the same ConfigurationError for dependency handling, return the existing request-time 503 response and detail where watch configuration is required, and preserve existing health endpoint behavior.

3.4 WHEN watch configuration is valid THEN the system SHALL CONTINUE TO apply the configured timezone, Redis URL, polling interval, jitter, and maximum attempts and use the existing Redis attachment or in-memory fallback behavior.
