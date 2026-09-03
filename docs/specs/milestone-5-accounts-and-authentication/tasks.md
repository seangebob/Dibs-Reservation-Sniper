# Implementation Plan

Each task is independently testable and leaves the anonymous flow working. Authentication is added
as an optional lens (no `Authorization` header ⇒ Milestone 1–4 behavior), so the suite stays green
task-by-task.

- [x] 1. Characterize and lock the pre-auth baseline
  - Write tests proving today's contract before auth exists: anonymous watch create/list/cancel work
    with no `Authorization` header; `GET /api/watches/{id}` and `DELETE` succeed by id with no owner
    check; no `/api/auth/*` routes exist; CORS does not yet allow `Authorization`.
  - Run `python -m pytest`, `python -m compileall backend tests`, `git diff --check`.
  - _Requirements: 7.1, 7.2_

- [x] 2. Add account configuration and the password hasher
  - Add `argon2-cffi` to `pyproject.toml`/`requirements.txt`. Add account settings (session TTL,
    password min length, argon2 params, throttle threshold) following `config.py`'s bounded
    `from_environment()` pattern; bad values raise `ConfigurationError`.
  - Add a small password-hashing module (`hash_password`, `verify_password`) wrapping argon2 with a
    constant-time dummy verify for unknown users.
  - _Requirements: 1.1, 1.6, 6.3_

- [x] 3. Add the `users` + `sessions` schema and repositories
  - Add an ordered migration (alongside M4's `watch_history` migration) creating `users` and
    `sessions` per design.md, and adding `watch_history.user_id` (nullable, indexed, FK).
  - Implement `AccountRepository` (`create_user`, `get_by_email`, `get_by_id`) and
    `SessionRepository` (`create`, `get_by_token_hash`, `revoke`, `revoke_all_for_user`).
  - _Bug_Condition: no durable account or session storage exists_
  - _Expected_Behavior: accounts and sessions persist in PostgreSQL; only argon2 + sha256 hashes are
    stored, never a recoverable secret_
  - _Requirements: 1.1, 2.1, 6.3_

- [x] 4. Build `AuthService` (signup / login / logout / authenticate)
  - Normalize email; enforce password policy; create user (duplicate → `EmailTakenError`); verify on
    login with a single generic `InvalidCredentialsError`; issue opaque tokens (return raw once,
    store `sha256`); `authenticate(token)` → user or `None`; `logout` revokes idempotently.
  - _Bug_Condition: credentials cannot be established or verified_
  - _Expected_Behavior: signup/login yield a session; wrong/unknown credentials give one generic,
    non-enumerating failure and mutate nothing_
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.1, 2.2, 2.3, 2.5_

- [x] 5. Add auth routes and wire accounts into `main.py`
  - `POST /api/auth/signup`, `/login`, `/logout`; `GET /api/auth/me`. Wire `AuthService` in the
    lifespan behind PostgreSQL availability, degrading to an "accounts unavailable" (503) state when
    Postgres is off — reusing `_attach_postgres`'s optional-feature philosophy — never blocking
    startup or the anonymous routes. Register the new exception handlers (409/401/422/503/429).
  - _Requirements: 1.1–1.6, 2.3, 2.4, 6.2, 7.2_

- [x] 6. Add the auth dependency and allow the `Authorization` CORS header
  - `current_user` (optional: valid bearer → user, else anonymous) and `require_user` (else 401)
    FastAPI dependencies. Add `Authorization` to `_CORS_ALLOWED_HEADERS`; keep explicit origins and
    `allow_credentials=False`. Assert via `TestClient` that a valid token authenticates and a
    missing/expired one is anonymous (not an error) on optional routes.
  - _Requirements: 2.2, 6.1_

- [x] 7. Enforce account ownership on watches
  - Record `user_id` on watch history when the creator is authenticated (prompt + direct create).
    Scope `GET /api/watches/mine` by `user_id` when authenticated, else by `owner_client_id` (M4).
    On `GET`/`DELETE /api/watches/{id}`, consult the projection: an account-owned watch requested by
    a different (or no) account → `404`; anonymous-owned or projection-disabled → by id as in M1–4.
  - _Preservation: the public `Watch` model gains no field; enforcement reads the projection only._
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 7.3_

- [x] 8. Claim anonymous watches on signup/login
  - `claim_anonymous(client_id, user_id)`: `UPDATE watch_history SET user_id WHERE
    owner_client_id = :c AND user_id IS NULL`. Call from signup and login when `X-Dibs-Client-Id` is
    present; idempotent and never re-assigns already-claimed watches.
  - _Requirements: 4.1, 4.2, 4.3, 4.4_

- [x] 9. Best-effort login throttle
  - A small sliding-window counter per email+origin (in-process, or Redis when present) returning
    `429` past the configured threshold; documented as best-effort, not account lockout.
  - _Requirements: 6.4_

- [x] 10. Frontend auth plumbing
  - `lib/auth.ts` (persist/clear `dibs.session-token`); extend `lib/api.ts` to attach
    `Authorization: Bearer` alongside `X-Dibs-Client-Id`, add `signup`/`login`/`logout`/`me`
    wrappers, and clear the token + drop to logged-out on a `401` from an auth-required call. Unit
    tests for header attachment, 401 handling, and token persistence.
  - _Requirements: 5.2, 5.3_

- [x] 11. Frontend auth UI
  - A sign-in / sign-up form (Night Scope styling); masthead shows the logged-in email + Log out;
    an `AuthProvider` hydrates the current user via `/me` on load. Dashboard shows the account's
    watches when authed and behaves exactly as M4 when logged out. Component tests for the form
    (success, error, logged-in vs logged-out masthead).
  - _Requirements: 5.1, 5.4, 5.5_

- [x] 12. Full validation
  - Backend `python -m pytest` (full suite — M1–4 regressions plus M5), `python -m mypy backend`,
    `python -m compileall backend tests`, `git diff --check`. Frontend `typecheck` + `vitest` +
    `next build`. Add an M5 preservation-baseline test asserting the no-`Authorization` flow is
    byte-identical, and confirm the M4 contract-drift test still passes unchanged.
  - Confirm every Requirement 1–7 acceptance criterion has a corresponding passing test, and that no
    Milestone 1–4 assertion was weakened.
  - _Requirements: all_
