# Requirements Document

## Introduction

Milestone 4 gave Dibs a web front door and a durable, owner-scoped watch history — but "owner"
meant an **anonymous** `X-Dibs-Client-Id` token in the browser, explicitly *not* a login (M4
Requirement 2.5: "there is no authentication to enforce a real access boundary yet ... documented
as a known limitation"). Milestone 5 closes that gap: real accounts, real sessions, and a real
access boundary on account-owned watches — built on the exact seam M4 left for it
(`watch_history.owner_client_id`, nullable and `COALESCE`-upserted).

Three scope decisions were made before writing this document and apply to every requirement below:

- **Email + password.** Accounts authenticate with an email and a hashed password (argon2). No
  external identity provider and no email-sending service is required to sign up or log in.
  Password reset (which needs email) is explicitly out of scope for this milestone.
- **Opaque server-side bearer sessions.** A logged-in session is a random opaque token sent as
  `Authorization: Bearer <token>`; only its hash is stored server-side, and it is individually
  revocable. This fits M4's header-based, `allow_credentials=False` CORS with no cookie/CSRF work.
- **Anonymous watches are claimed.** When a visitor who already has anonymous watches signs up or
  logs in, their browser's `X-Dibs-Client-Id` watches are linked to the new account.

Two non-negotiable constraints carry over from M4: the anonymous flow (scripts, tests, and
first-time visitors with no account) MUST keep working exactly as today, and every Milestone 1–4
test MUST keep passing unmodified.

## Requirement 1: Account creation and login

**User Story:** As a visitor, I want to create an account with an email and password and log back
into it later, so my watches are tied to me and not just to one browser.

#### Acceptance Criteria

1.1 WHEN a visitor submits a well-formed email and a password meeting the minimum policy to the
    signup endpoint AND no account already exists for that email THEN the system SHALL create an
    account storing only an argon2 hash of the password (never the plaintext), and SHALL establish a
    session (Requirement 2) in the same response.
1.2 WHEN a visitor submits credentials matching an existing account to the login endpoint THEN the
    system SHALL verify the password against the stored hash and establish a new session.
1.3 WHEN signup is attempted for an email that already has an account THEN the system SHALL reject
    it with a clear, non-enumerating error and SHALL NOT reveal whether the failure was a duplicate
    versus another validation problem beyond what a normal signup form must show.
1.4 WHEN login is attempted with an unknown email OR a wrong password THEN the system SHALL return a
    single generic "invalid email or password" failure that does not disclose which of the two was
    wrong, and SHALL NOT lock, delete, or mutate any account.
1.5 WHEN an email is submitted THEN the system SHALL normalize it (trim, lower-case) before
    uniqueness checks and storage, so `A@x.com` and `a@x.com` are the same account.
1.6 WHEN a password shorter than the minimum length (or otherwise failing the documented policy) is
    submitted THEN the system SHALL reject signup with a plain-language message and SHALL NOT create
    an account.

## Requirement 2: Session management

**User Story:** As a logged-in user, I want to stay logged in across requests and be able to log
out, so I control my session.

#### Acceptance Criteria

2.1 WHEN a session is established (signup or login) THEN the system SHALL generate a
    cryptographically random opaque token, persist only its hash with an owner, a creation time, and
    an expiry, and return the raw token to the client exactly once.
2.2 WHEN a request presents `Authorization: Bearer <token>` for a token whose hash matches a
    non-expired session THEN the system SHALL treat the request as authenticated as that session's
    user; WHEN the token is missing, malformed, unknown, or expired THEN the system SHALL treat the
    request as anonymous (not error) except on endpoints that explicitly require authentication.
2.3 WHEN a user logs out THEN the system SHALL revoke the presented session so the token can never
    authenticate again, and SHALL return success even if the token was already invalid.
2.4 WHEN the current user requests their own profile (`GET /api/auth/me`) with a valid session THEN
    the system SHALL return their non-sensitive account fields (id, email, created_at) and never the
    password hash or session token.
2.5 WHEN a session is older than the documented maximum lifetime THEN the system SHALL treat it as
    expired and require a fresh login, without deleting the account.

## Requirement 3: Authenticated watch ownership and access control

**User Story:** As a logged-in user, I want my watches to be truly mine — only I can see and cancel
them — so anonymous scoping becomes a real boundary once I have an account.

#### Acceptance Criteria

3.1 WHEN an authenticated user creates a watch (via prompt or direct API) THEN the system SHALL
    record the watch's durable projection with that user as owner, in addition to the existing
    `owner_client_id` behavior.
3.2 WHEN an authenticated user lists "my watches" THEN the system SHALL return watches owned by
    their **account**, superseding client-id-only scoping for that user; WHEN an unauthenticated
    caller lists with only a client id THEN the system SHALL behave exactly as in Milestone 4.
3.3 WHEN an authenticated user requests `GET`/`DELETE` on a watch owned by a **different** account
    THEN the system SHALL deny it (404, indistinguishable from "not found", to avoid leaking
    existence) rather than returning or cancelling it.
3.4 WHEN `GET`/`DELETE` is requested on an **anonymous** watch (no account owner) THEN the system
    SHALL CONTINUE to allow it by id exactly as in Milestones 1–4, since removing that would break
    existing callers; the new boundary applies only to account-owned watches.
3.5 WHEN the durable ownership record is unavailable (PostgreSQL disabled) THEN account-scoped
    features SHALL degrade safely — authentication still works, but ownership enforcement and "my
    watches" behave as the anonymous case rather than failing the request.

## Requirement 4: Claiming anonymous watches

**User Story:** As a visitor who made watches before signing up, I want them to appear under my new
account, so I don't lose what I already started.

#### Acceptance Criteria

4.1 WHEN a visitor signs up or logs in AND presents an `X-Dibs-Client-Id` that owns anonymous
    watches (no account owner yet) THEN the system SHALL link those watches' durable records to the
    authenticated account.
4.2 WHEN a watch is claimed THEN it SHALL thereafter appear in that account's "my watches" and be
    subject to the Requirement 3 access boundary, and SHALL NOT be double-claimed by a later login
    from the same client id.
4.3 WHEN a client id owns watches that are already claimed by a *different* account THEN the system
    SHALL NOT re-assign them; claiming only affects watches with no current account owner.
4.4 WHEN no `X-Dibs-Client-Id` is present at signup/login THEN signup/login SHALL still succeed and
    simply claim nothing.

## Requirement 5: Frontend authentication experience

**User Story:** As a visitor, I want to sign up, log in, see that I'm logged in, and log out from
the web app, so the account features are usable without `curl`.

#### Acceptance Criteria

5.1 WHEN a visitor opens the app THEN the frontend SHALL offer sign-up and log-in, and WHEN
    authenticated SHALL show the logged-in identity (email) and a log-out action in the masthead.
5.2 WHEN a session token is obtained THEN the frontend SHALL persist it, attach it as
    `Authorization: Bearer <token>` on API calls, and continue to send `X-Dibs-Client-Id` so the
    backend can claim anonymous watches on the first authenticated call.
5.3 WHEN the frontend holds a token that the backend reports invalid/expired (401 on an
    auth-required call) THEN the frontend SHALL clear the stored token and return to a logged-out
    state without a crash or a raw error.
5.4 WHEN an authenticated user views the watch dashboard THEN it SHALL show their account's watches;
    WHEN logged out THEN it SHALL behave exactly as in Milestone 4 (anonymous client-id scoping).
5.5 WHEN any auth request fails THEN the frontend SHALL show a plain-language message and SHALL NOT
    display a stack trace, raw JSON, or the password the user typed.

## Requirement 6: Cross-origin, configuration, and security posture

**User Story:** As the operator, I want accounts to be secure by default and to not break the
backend's existing standalone operation.

#### Acceptance Criteria

6.1 WHEN the browser sends the `Authorization` header cross-origin THEN the backend CORS policy
    SHALL permit it for the configured frontend origin(s), continuing to use explicit origins (never
    `*`) and `allow_credentials=False`.
6.2 WHEN account tables are needed THEN their schema SHALL be applied by the same automatic
    startup-migration mechanism M4 introduced, and WHEN PostgreSQL is not configured THEN the
    backend SHALL still start and serve all Milestone 1–4 routes, with account endpoints reporting a
    clear "accounts unavailable" state rather than crashing.
6.3 WHEN passwords or session tokens are stored THEN the system SHALL store only an argon2 password
    hash and only a hash of each session token — never a recoverable secret — and SHALL keep both
    out of every API response and log line.
6.4 WHEN repeated failed logins target an account or origin THEN the system SHOULD apply a basic
    throttle/backoff to blunt brute-force attempts, documented as best-effort rather than a full
    account-lockout policy.

## Requirement 7: Preservation of existing contracts

**User Story:** As the maintainer of Milestones 1–4, I want accounts added without changing any
behavior those milestones' tests already lock in.

#### Acceptance Criteria

7.1 WHEN this milestone is complete THEN the system SHALL CONTINUE to pass every existing test in
    `tests/` and `frontend/` unmodified in its assertions.
7.2 WHEN a request carries no session THEN every Milestone 1–4 endpoint SHALL behave byte-for-byte
    as before, including anonymous watch creation, `X-Dibs-Client-Id` listing, and unscoped
    `GET /api/watches`.
7.3 WHEN the public `Watch`, `PromptExecutionResult`, and health contracts are served THEN any new
    account field SHALL be additive and absent-safe, and SHALL NOT alter the existing JSON shapes the
    M4 contract-drift test pins.
