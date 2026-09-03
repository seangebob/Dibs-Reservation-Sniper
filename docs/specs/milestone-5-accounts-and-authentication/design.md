# Design Document

## Overview

Milestone 5 adds email/password accounts and opaque server-side bearer sessions, then upgrades
Milestone 4's anonymous `owner_client_id` scoping into a real, enforced access boundary for
account-owned watches — while leaving every anonymous/script path untouched. It is additive
infrastructure in the same spirit as M4: PostgreSQL holds the new durable tables, the anonymous flow
is the fallback everywhere, and nothing in the fenced Milestone-3 polling protocol changes.

Guiding principle: **authentication is an optional lens over the existing system, never a gate in
front of it.** A request with no `Authorization` header behaves exactly as it did in Milestone 4.

## Architecture

```
Browser ──Authorization: Bearer <tok>──▶ FastAPI
  │  (also still sends X-Dibs-Client-Id)      │
  │                                           ├─ auth dependency: hash(tok) → session → user | anonymous
  │                                           ├─ AuthService (argon2 verify, session issue/revoke, claim)
  │                                           └─ WatchService / PromptRouter (unchanged core)
  └─ localStorage: dibs.session-token + dibs.client-id
PostgreSQL: users, sessions, watch_history(+user_id)      Redis/in-memory: live watch store (unchanged)
```

New backend modules (mirroring the existing layout):

| Module | Responsibility |
| --- | --- |
| `backend/models/account.py` | `User` (public: id, email, created_at — no hash), `Session` internal model |
| `backend/db/repositories/accounts.py` | `AccountRepository` (users) + `SessionRepository` (sessions, claim) |
| `backend/services/auth_service.py` | signup / login / logout / `authenticate(token)` / `claim_anonymous(...)` |
| `backend/api/routes/auth.py` | `POST /api/auth/signup`, `/login`, `/logout`; `GET /api/auth/me` |
| `backend/api/auth_dependency.py` | `current_user` (optional) and `require_user` FastAPI dependencies |
| `backend/db/migrations/*.sql` | additive migration: `users`, `sessions`, `watch_history.user_id` |

## Data model

**`users`**

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `uuid` PK | `gen_random_uuid()` |
| `email` | `text` | stored normalized (trim + lower); `UNIQUE` |
| `password_hash` | `text` | argon2 encoded hash; never leaves the DB layer |
| `created_at` / `updated_at` | `timestamptz` | |

**`sessions`** (opaque bearer tokens — only the hash is stored)

| Column | Type | Notes |
| --- | --- | --- |
| `token_hash` | `text` PK | `sha256(raw_token)` hex; raw token returned to client once, never stored |
| `user_id` | `uuid` FK → `users.id` `ON DELETE CASCADE` | indexed for logout-all |
| `created_at` / `expires_at` | `timestamptz` | expiry = created + `SESSION_TTL` |
| `last_used_at` | `timestamptz` null | optional sliding-window bump |

**`watch_history`** gains one additive column: `user_id uuid NULL` (FK → `users.id`), indexed for
account-scoped listing. `owner_client_id` stays exactly as M4 built it; claiming fills `user_id`.

## Key decisions

- **argon2 for passwords** (`argon2-cffi`, OWASP's default recommendation) — a single new runtime
  dependency, sensible defaults, self-verifying encoded hashes (no separate salt column). Minimum
  password length is a documented policy constant (default 8).
- **Opaque tokens, hash-at-rest.** `secrets.token_urlsafe(32)` → returned once; DB stores only
  `sha256`. A DB leak yields no usable tokens, and logout/expiry are a single-row delete/filter.
  Chosen over JWT for trivial revocation and over cookies to keep `allow_credentials=False` +
  header-based CORS with zero CSRF surface.
- **Ownership enforced via the history projection, not the public `Watch` model.** M4 deliberately
  kept `owner_client_id` out of the public `Watch` JSON; M5 keeps `user_id` out too. `GET`/`DELETE
  /api/watches/{id}` consult `watch_history` for an account owner and enforce only when one exists
  (see Access control). This preserves the M4 contract-drift test and the public shape (Req 7.3).
- **Authentication degrades, never blocks.** No `Authorization` header → anonymous, exactly as M4.
  PostgreSQL disabled → account endpoints report "accounts unavailable"; the anonymous system runs
  untouched (Req 3.5 / 6.2). This reuses M4's `_attach_postgres` optional-feature philosophy.

## Endpoints

| Method + path | Body / auth | Returns |
| --- | --- | --- |
| `POST /api/auth/signup` | `{email, password}` (+ optional `X-Dibs-Client-Id`) | `{token, user}` + claims anon watches |
| `POST /api/auth/login` | `{email, password}` (+ optional `X-Dibs-Client-Id`) | `{token, user}` + claims anon watches |
| `POST /api/auth/logout` | `Authorization: Bearer` | `204`; revokes the presented session |
| `GET /api/auth/me` | `Authorization: Bearer` | `{id, email, created_at}` or `401` |

Watch routes gain an **optional** `current_user` dependency (never required):
- `POST /api/watches` and prompt-created watches record `user_id` when authenticated (Req 3.1).
- `GET /api/watches/mine`: authenticated → scope by `user_id`; else by `owner_client_id` (M4).
- `GET`/`DELETE /api/watches/{id}`: if the projection shows an account owner and it isn't the
  caller → `404` (indistinguishable from missing, Req 3.3); anonymous-owned or no-projection → by
  id as in M1–4 (Req 3.4/3.5).

## Auth flow details

- **Signup:** validate email shape + password policy → normalize email → `INSERT` (unique violation
  → `EmailTakenError` → non-enumerating 409) → issue session → if `X-Dibs-Client-Id` present, claim.
- **Login:** normalize email → fetch user → argon2 `verify` (constant-time; do a dummy verify when
  the email is unknown to equalize timing) → on success issue session + claim; on any failure a
  single generic 401.
- **authenticate(token):** `sha256` the bearer → session lookup where `expires_at > now()` → user,
  else anonymous.
- **claim_anonymous(client_id, user_id):** `UPDATE watch_history SET user_id=:u WHERE
  owner_client_id=:c AND user_id IS NULL` — idempotent, never steals already-claimed watches
  (Req 4.2/4.3).

## Security posture

- Only argon2 password hashes and `sha256` token hashes persisted; both excluded from every response
  model and log line (Req 6.3). The public `User` model has no `password_hash` field at all.
- Login/signup return generic messages; timing equalized on unknown email (Req 1.4).
- Best-effort brute-force throttle: a small per-email+origin sliding-window counter (in-process, or
  Redis when present) returning `429` past a threshold — documented as best-effort, not lockout
  (Req 6.4).
- CORS: add `Authorization` to the allowed request headers; keep explicit origins and
  `allow_credentials=False` (Req 6.1).

## Frontend

- `lib/auth.ts`: get/set/clear `dibs.session-token` in `localStorage`; a small `AuthProvider`
  React context exposing `{ user, signup, login, logout, loading }`, hydrated on load via `/me`.
- `lib/api.ts`: attach `Authorization: Bearer` when a token exists (alongside `X-Dibs-Client-Id`);
  add `signup`/`login`/`logout`/`me` wrappers; on a `401` from an auth-required call, clear the token
  and drop to logged-out (Req 5.3).
- UI: a sign-in/sign-up form (modal or `/account`), masthead shows email + Log out when authed
  (Req 5.1). Dashboard is unchanged in code — `/mine` returns account watches once the header is
  attached (Req 5.4).

## Error handling

| Condition | Exception | Status |
| --- | --- | --- |
| Email already registered | `EmailTakenError` | 409 (non-enumerating) |
| Bad email/password on login | `InvalidCredentialsError` | 401 (generic) |
| Weak/short password | validation | 422 |
| Auth-required route, no/again-invalid session | `AuthenticationRequiredError` | 401 |
| Accounts endpoint, PostgreSQL disabled | `AccountsUnavailableError` | 503 |
| Throttle threshold exceeded | `RateLimitedError` | 429 |

All handlers return `{ "detail": "..." }`, matching the shape `frontend/lib/api.ts` already parses.

## Preservation

- No `Authorization` header ⇒ identical M1–4 behavior on every route (Req 7.2).
- New DB tables + column are additive migrations; Postgres-off keeps the backend fully serving
  (Req 6.2). New Python dependency: `argon2-cffi`.
- Public `Watch` / `PromptExecutionResult` / `/health` shapes unchanged; `user_id` lives only in the
  projection, so the M4 contract-drift test stays green (Req 7.3). A new M5 preservation-baseline
  test asserts the anonymous flow is byte-identical.
