# Requirements Document

## Introduction

Dibs can watch a reservation, take the shot, and record the result durably — but it cannot tell
anyone. `LoggingNotificationService` writes a log line and that is the entire outbound story; its
own docstring has promised `integrations/email.py` since Milestone 3. The consequence is that
Milestone 3's lease-fenced, crash-recoverable, exactly-once coordination exists to deliver a
`logger.info` that no user will ever read. A watch that finds a table at 2am notifies nobody.

Milestone 6 closes that gap. It is the natural payoff of Milestone 5: before accounts there was no
address to send to, and now `users.email` exists.

Three scope decisions were made before writing this document and apply to every requirement below:

- **SMTP via the standard library.** Delivery uses `smtplib` against any configured relay (Gmail,
  SendGrid, Mailgun, Postmark). No new dependency, and the provider stays a configuration value
  rather than a vendor lock in code.
- **The worker is in scope.** The Celery worker currently has no PostgreSQL wiring at all and never
  passes a history recorder or notifier, so background poll outcomes reach neither the projection
  nor a notification. Since polls are exactly where terminal events fire, email would silently do
  nothing in the documented `app` compose profile without this. That profile already sets
  `POSTGRES_URL` for the worker with the comment "Same projection so background poll outcomes are
  recorded to history too" — the intent is declared and the code drops it.
- **Best-effort delivery, honestly documented.** No durable outbox, no delivery state machine, no
  automatic replay. A failed send is logged and abandoned rather than retried, so a user is never
  emailed twice about the same event. The durable record remains the dashboard.

Two non-negotiable constraints carry over: a deployment with no SMTP configuration MUST behave
exactly as it does today, and every Milestone 1–5 test MUST keep passing unmodified.

## Requirement 1: Email delivery for terminal watch events

**User Story:** As someone who set Dibs on a table, I want an email the moment it finds or books
one, so I find out without having to keep the dashboard open.

#### Acceptance Criteria

1.1 WHEN a watch owned by an account reaches `AVAILABILITY_FOUND`, `BOOKED`, or `EXPIRED` AND email
    delivery is configured THEN the system SHALL send one email to that account's address describing
    the event, the venue, the date, and the party size.
1.2 WHEN the email is composed THEN it SHALL be a plain-text message with a subject naming the venue
    and the outcome, and SHALL contain a link back to the watch dashboard.
1.3 WHEN a `BOOKED` event is sent THEN the message SHALL include the booking confirmation
    identifier, so the recipient can act on it without opening the app.
1.4 WHEN delivery is configured with a relay requiring authentication and/or STARTTLS THEN the
    system SHALL authenticate and negotiate TLS as configured.
1.5 WHEN the same watch transitions terminally more than once (it cannot, by Milestone 3's terminal
    authority) THEN no more than one email per event SHALL be sent.

## Requirement 2: Recipient resolution

**User Story:** As an anonymous visitor, I want Dibs to keep working exactly as before, so not
having an account never becomes an error.

#### Acceptance Criteria

2.1 WHEN a terminal event fires for a watch THEN the system SHALL resolve the recipient by reading
    the watch's account owner from the durable projection and that account's email address.
2.2 WHEN the watch has no account owner (anonymous, Milestone 4 style) THEN the system SHALL send
    nothing, treat it as a normal outcome, and SHALL NOT log an error.
2.3 WHEN the durable projection is unavailable (PostgreSQL not configured) THEN recipient resolution
    SHALL degrade to "no recipient" rather than failing the watch transition.
2.4 WHEN the account owner exists but the account has since been deleted THEN the system SHALL send
    nothing rather than raising.
2.5 WHEN resolution requires reading account data THEN it SHALL reuse the existing repository
    methods and SHALL NOT add a new query path or place `user_id` on the public `Watch` model.

## Requirement 3: Notification must never affect a live watch outcome

**User Story:** As the operator, I want a broken mail server to cost me an email and nothing else,
so outbound delivery can never corrupt watch state.

#### Acceptance Criteria

3.1 WHEN a notification raises for any reason THEN the system SHALL log it and continue, and the
    watch's committed status, poll result, and returned value SHALL be byte-identical to a run in
    which the notification succeeded.
3.2 WHEN a notification is slow THEN it SHALL be bounded by a configured timeout, so a poll holding
    a Milestone 3 lease cannot be delayed past it by outbound delivery.
3.3 WHEN a notification fails THEN the durable history projection SHALL still be written, and the
    poll SHALL NOT be retried on account of the notification.
3.4 WHEN a terminal transition commits THEN the durable projection SHALL be recorded BEFORE the
    notification is attempted, so the dashboard is correct even if delivery fails.
3.5 WHEN blocking SMTP work runs inside the async service THEN it SHALL NOT block the event loop.

## Requirement 4: Worker parity

**User Story:** As the operator running the documented compose profile, I want background polls to
behave exactly like in-process polls, so a distributed deployment is not quietly less capable.

#### Acceptance Criteria

4.1 WHEN the Celery worker builds its watch service AND `POSTGRES_URL` is configured THEN it SHALL
    wire the same durable history recorder the API process uses, so background poll outcomes update
    the projection.
4.2 WHEN the worker is configured for email THEN it SHALL wire the same notification service the API
    process uses, so terminal events discovered in the background are delivered.
4.3 WHEN the worker has no PostgreSQL configured THEN it SHALL start and poll exactly as it does
    today, with no projection and no email.
4.4 WHEN the worker initializes PostgreSQL resources THEN it SHALL close them on shutdown alongside
    its existing Redis cleanup, exactly once.
4.5 WHEN the worker records history or notifies THEN failures SHALL follow Requirement 3 and SHALL
    NOT enter the worker's recoverable retry classification.

## Requirement 5: Configuration and degradation

**User Story:** As a developer running Dibs locally, I want it to work with no mail configuration at
all, so nothing new is required to get started.

#### Acceptance Criteria

5.1 WHEN no SMTP configuration is present THEN the system SHALL use the existing logging notifier and
    behave exactly as it does today.
5.2 WHEN SMTP configuration is present but invalid THEN the system SHALL raise `ConfigurationError`
    through the established bounded `from_environment()` pattern, and SHALL degrade to the logging
    notifier with a logged error rather than failing startup.
5.3 WHEN email settings are read THEN host, port, username, password, sender address, TLS mode, and
    send timeout SHALL each be configurable, with bounded validation on the numeric values.
5.4 WHEN a dashboard link is included in a message THEN its base URL SHALL be configurable and SHALL
    fall back to a safe default rather than failing.

## Requirement 6: Privacy and preservation

**User Story:** As the maintainer, I want outbound delivery added without leaking data into logs or
changing anything that already works.

#### Acceptance Criteria

6.1 WHEN a watch transition is logged by the notification layer THEN the log line SHALL NOT contain
    the venue name, reservation date, or party size; the watch id, event, and attempt count remain.
6.2 WHEN SMTP credentials are configured THEN they SHALL NOT appear in any log line, exception
    message, or settings representation.
6.3 WHEN a recipient address is handled THEN it SHALL NOT be written to logs at any level.
6.4 WHEN this milestone is complete THEN every existing test in `tests/` and `frontend/` SHALL
    continue to pass unmodified in its assertions, with ONE documented exception: 6.1 deliberately
    changes the notification log line, so
    `test_logging_config.py::test_notification_event_is_terminal_visible_once_after_lifespan_starts`
    updates the literal it compares against. That test's actual guarantee — the event is visible
    exactly once, on exactly one handler, after the lifespan starts — is unchanged; only the payload
    it happens to carry is. No other assertion may be weakened or rewritten.
6.5 WHEN the public `Watch`, `PromptExecutionResult`, and `/health` contracts are served THEN they
    SHALL be unchanged, and the Milestone 4 contract-drift test SHALL pass untouched.
