# Requirements Document

## Introduction

Milestones 1–3 built the reasoning, mock-booking, and coordination engine entirely behind
`/api/parse-and-book` and `/api/watches`, exercised only by scripts and tests. Milestone 4 gives
that engine a real front door: a web UI where a person types a natural-language reservation
request, sees the immediate result, and — when a watch is created — can come back later and find
it still there.

"Still there" is the operative requirement. Today a watch's only durable home is the
`WatchRepository` seam from Milestone 3 (Redis, or in-memory with no cross-restart durability at
all). That seam was deliberately built to be swapped, and this milestone is the swap: watches
become rows in PostgreSQL that outlive a Redis flush, a Redis-mode downgrade to in-memory, or a
full infrastructure rebuild — without touching the fenced single-flight polling protocol Milestone
3 spent ten tasks getting right.

Two scope decisions were made before writing this document and apply to every requirement below:

- **No authentication in this milestone.** "User-owned" means scoped to an anonymous
  client-generated identifier persisted in the browser, not a login. Real accounts are Milestone 5
  scope; this milestone must not preclude adding them later.
- **Frontend is a separate Next.js app** (`apps/web`), calling the existing FastAPI backend over
  HTTP, matching the `apps/web` + `apps/api` split already sketched in this repo's scratch system
  design.

## Requirement 1: Natural-language web UI

**User Story:** As a visitor, I want to type a plain-English reservation request into a web page
and see what happened, so I don't need `curl` or a script to use Dibs.

#### Acceptance Criteria

1.1 WHEN a visitor loads the web app's root page THEN the system SHALL render a single text input
    for a free-form reservation prompt and a submit action, with no separate fields for party size,
    date, or venue.
1.2 WHEN a visitor submits a prompt THEN the frontend SHALL call the existing
    `POST /api/parse-and-book` contract unchanged and SHALL disable the input while the request is
    in flight.
1.3 WHEN the response status is `CLARIFICATION_REQUIRED` THEN the system SHALL display the
    returned `clarification_question` and let the visitor answer in the same input without losing
    the page state.
1.4 WHEN the response status is `AVAILABILITY_FOUND` or `NO_AVAILABILITY` THEN the system SHALL
    display the considered slots (if any) without implying a reservation was made.
1.5 WHEN the response status is `MOCK_BOOKED` THEN the system SHALL display the confirmed slot and
    SHALL visibly label it as a mock/demo confirmation, not a real reservation.
1.6 WHEN the response status is `WATCH_CREATED` THEN the system SHALL display the created watch and
    add it to the visitor's watch list (Requirement 2) without requiring a page reload.
1.7 WHEN the backend returns an HTTP error (422 validation, 503 configuration, 5xx) THEN the system
    SHALL show a plain-language failure message and SHALL NOT show a stack trace, raw JSON, or the
    words "500"/"exception" to the visitor.

## Requirement 2: Anonymous client identity

**User Story:** As a visitor without an account, I want the site to remember which watches are
mine between visits, so I don't lose track of them without having to sign up for anything.

#### Acceptance Criteria

2.1 WHEN a visitor first loads the web app AND no client identifier exists in browser storage THEN
    the system SHALL generate a new opaque identifier client-side and persist it (e.g.
    `localStorage`) before the first API call that creates or lists a watch.
2.2 WHEN the frontend calls a watch-creating or watch-listing endpoint THEN it SHALL send the
    stored client identifier in a request header (`X-Dibs-Client-Id`), and the backend SHALL accept
    and validate its shape (bounded-length opaque token) without treating it as authentication.
2.3 WHEN a watch is created with a client identifier present THEN the system SHALL persist that
    identifier as the watch's `owner_client_id` and SHALL use it to scope the visitor's watch list
    (Requirement 3) to only their own watches.
2.4 WHEN a request creates a watch with no client identifier (an existing script, test, or direct
    API caller) THEN the system SHALL CONTINUE to accept it exactly as today, persisting
    `owner_client_id` as `null` and leaving it invisible to any owner-scoped list but still visible
    through the existing unscoped `GET /api/watches` behavior.
2.5 WHEN a client identifier is presented for `GET`/`DELETE` on a specific watch THEN the system
    SHALL NOT use it to deny access to that watch by ID — ownership scoping applies only to listing
    "my watches" in this milestone, since there is no authentication to enforce a real access
    boundary yet. This SHALL be documented as a known limitation, not silently treated as security.

## Requirement 3: Durable, queryable watch history

**User Story:** As a visitor, I want my watch to still show up after the server restarts or Redis
is flushed, so a monitoring outage doesn't make my reservation request disappear.

#### Acceptance Criteria

3.1 WHEN a watch is created, updated, or reaches a terminal status THEN the system SHALL persist a
    durable projection of its current public fields (identity, query, status, timestamps, found
    slots, booking, owner) to PostgreSQL, in addition to — not instead of — the existing
    `WatchRepository` record that drives active polling.
3.2 WHEN PostgreSQL is unreachable at the moment of a projection write THEN the system SHALL log
    the failure and SHALL NOT fail, roll back, or delay the watch-creation or poll-outcome request
    that triggered it; the coordinated Redis/in-memory protocol from Milestone 3 remains
    authoritative for correctness regardless of Postgres availability.
3.3 WHEN a visitor requests their watch list (`GET /api/watches?owner=...` or equivalent) AND the
    watch's live record has already been cleaned up by Milestone 3's terminal retention policy
    THEN the system SHALL still return the durable PostgreSQL projection for any watch within a
    documented history retention window, clearly distinguishing "no longer actively tracked" from
    "never existed."
3.4 WHEN the application starts THEN the system SHALL run its PostgreSQL schema migration
    automatically (or fail startup with a clear error) rather than requiring a manual out-of-band
    step before the API can serve requests.
3.5 WHEN a watch's durable projection and its live Redis/in-memory record disagree (e.g., a crash
    between the live commit and the projection write) THEN the discrepancy SHALL be resolved in
    favor of the live record for any watch still within Milestone 3's active-index or retention
    window, since the projection is a best-effort mirror, not a second source of truth for
    in-flight coordination.

## Requirement 4: Watch dashboard

**User Story:** As a visitor with one or more watches running, I want a page listing them with
their live status, so I can see what's still being monitored without re-submitting a prompt.

#### Acceptance Criteria

4.1 WHEN a visitor navigates to the watch dashboard THEN the system SHALL list every watch owned by
    their client identifier, most recently created first, showing venue, party size, date, status,
    and attempt count.
4.2 WHEN a listed watch is `ACTIVE` THEN the system SHALL show its next scheduled check time when
    available and SHALL allow the visitor to cancel it.
4.3 WHEN a visitor cancels a watch from the dashboard THEN the system SHALL call the existing
    `DELETE /api/watches/{watch_id}` contract unchanged and SHALL update the displayed status
    without a full page reload.
4.4 WHEN a listed watch reaches `FOUND` or `BOOKED` THEN the system SHALL surface that outcome
    prominently (not identically to a still-active watch) the next time the dashboard is viewed or
    polled, satisfying the notification path without requiring a push/email channel in this
    milestone.
4.5 WHEN the visitor has zero watches THEN the system SHALL show an empty state directing them back
    to the prompt input, not a blank list or an error.

## Requirement 5: Cross-origin and deployment wiring

**User Story:** As the operator, I want the new frontend and the existing backend to work together
both locally and in a deployed environment, without breaking the backend's existing standalone
operation.

#### Acceptance Criteria

5.1 WHEN the frontend origin differs from the API origin (local dev: `localhost:3000` vs
    `localhost:8000`) THEN the backend SHALL send CORS headers permitting the configured frontend
    origin(s) for the specific methods and headers Requirement 1 and 2 require, and SHALL NOT
    default to `*` when credentials or the client-identifier header are involved.
5.2 WHEN the backend is run without a frontend at all (existing scripts, tests, milestone 1–3
    workflows) THEN it SHALL CONTINUE to start and serve `/api/*` and `/health` exactly as before;
    the frontend SHALL be additive infrastructure, never a startup dependency of the API process.
5.3 WHEN `docker compose -f infra/docker-compose.yml --profile app up` is run THEN an optional `web`
    service SHALL be available building the Next.js app and pointing it at the compose-network API
    URL, without altering the existing `redis`, `postgres`, `api`, or `worker` service definitions,
    ports, images, or health checks.
5.4 WHEN environment configuration for the frontend's API base URL or the backend's allowed origins
    is missing THEN the system SHALL fail with a clear configuration error at startup (frontend
    build or backend request-serving) rather than silently pointing at `localhost` in a deployed
    environment.

## Requirement 6: Preservation of existing contracts

**User Story:** As the maintainer of Milestones 1–3, I want this milestone to add a UI and
persistence layer without changing any behavior those milestones' tests already lock in.

#### Acceptance Criteria

6.1 WHEN this milestone's changes are complete THEN the system SHALL CONTINUE to pass every
    existing test in `tests/`, including the full `milestone-3-production-path-hardening` and
    `watch-route-worker-retry-hardening` suites, unmodified in their assertions.
6.2 WHEN a watch is created, polled, cancelled, or expired THEN the system SHALL CONTINUE to use
    the exact public `Watch` JSON fields, `WatchStatus` values, and `WatchPollOutcome` values
    already in place; `owner_client_id` and any other new field SHALL be additive and
    `None`/absent-safe for every existing caller.
6.3 WHEN the fenced single-flight claim protocol, recovery coordinator, or readiness tracker from
    Milestone 3 runs THEN this milestone SHALL NOT alter their logic; the PostgreSQL projection
    SHALL be a passive observer of their outcomes (e.g., via the existing notification hook or an
    equivalent read-only tap), never a participant in claim/fencing decisions.
6.4 WHEN `GET /health` is requested THEN the system SHALL CONTINUE to report the existing fields
    unchanged and MAY additively report PostgreSQL projection health using the same
    ready/degraded/unknown evidence-based vocabulary Milestone 3 established, without changing the
    top-level `status` meaning.
