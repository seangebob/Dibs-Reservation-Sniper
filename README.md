# Dibs

**A reservation sniper for Kitchener–Waterloo.** Describe what you want in plain
English — *"watch A Restaurant for four next Saturday between 6 and 9"* — and Dibs
opens a durable background watch, polls for availability on a jittered cadence, books
the moment a table appears, and emails you.

> [!IMPORTANT]
> **Dibs books against a built-in mock provider, not a real one.** Everything below
> the provider seam is production-grade — distributed coordination, fenced claims,
> crash recovery, accounts, email — but no real venue is ever contacted. A booking
> returns status `MOCK_CONFIRMED`, which by design can never be mistaken for a table
> a venue is actually holding.

## How it works

```
   "book A Restaurant for four next Saturday at 7"
                      │
                      ▼
        API gateway — auth, rate limiting
                      │
                      ▼
   AI orchestration — the LLM extracts, code
   validates. Never the other way round.
              │              │
       complete              missing details
              ▼              ▼
   Book · Search · Watch     Ask one follow-up
              │
              ▼
   Background workers — poll, book, notify
```

1. **Extraction.** An LLM turns untrusted prose into a strict schema. That is *all* it
   does — it never decides anything.
2. **Validation.** Application code independently checks the result and picks exactly
   one route: book, watch, or ask a clarifying question. The model cannot override it.
3. **Execution.** A ready intent books immediately, searches, or opens a watch that
   survives restarts and keeps checking until the table appears or the date passes.

A watch polls on a jittered cadence (180s ± 30s — a perfectly regular one is trivially
identifiable as a bot), survives crashes and restarts, never fans out into duplicate
polling chains, and books idempotently so a redelivered job can't reserve twice.

## Status

| Layer | State |
| --- | --- |
| Intent extraction and validation | **Real** — needs an OpenAI key with credit |
| Watch engine: scheduling, fencing, recovery, retention | **Real** |
| Accounts and sessions | **Real** — argon2id + opaque bearer tokens |
| Email notification | **Real** — any SMTP relay |
| Durable history | **Real** — PostgreSQL |
| **Reservation provider** | **Simulated** — mock adapter only |

The provider seam is held to a conformance suite that runs the same 21 cases against
two independent implementations, so a real adapter is one class rather than a
refactor. It needs partner API access to go further.

## Running it locally

**Requirements:** Python 3.12+. Node 20+ for the frontend. Docker only if you want
Redis and PostgreSQL, which are upgrades rather than requirements.

### 1. Minimal — API only, no infrastructure

With no Redis and no PostgreSQL the app boots on an in-memory store and an in-process
queue. Everything works; nothing survives a restart.

```bash
git clone <this repo> && cd Reservation
python -m venv .venv
```

```bash
# macOS / Linux
source .venv/bin/activate
pip install -e ".[test]"
export OPENAI_API_KEY="sk-..."
uvicorn backend.main:app --reload
```

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
pip install -e ".[test]"
$env:OPENAI_API_KEY = "sk-..."
uvicorn backend.main:app --reload
```

Open <http://localhost:8000/docs> for the interactive API, or
<http://localhost:8000/health> to see which store and queue actually got bound.

Without `OPENAI_API_KEY` the server still starts — only the two prompt endpoints
return `503`. Everything else, including creating watches through
`POST /api/watches`, works without it.

### 2. No API key, no services — just watch the engine run

```bash
PYTHONPATH=. python misc/scripts/watch_demo.py
```

Drives a watch through its whole lifecycle in-process, using scripted intents and the
mock provider. Nothing external required.

### 3. Full local stack

Redis makes watches restart-durable and lets a real Celery worker do the polling;
PostgreSQL adds the durable history projection that powers accounts and
`/api/watches/mine`. Each command below is long-running — **use separate terminals**.

```bash
# Terminal 1 — Redis + PostgreSQL
docker compose -f infra/docker-compose.yml up -d

# Terminal 2 — API
export OPENAI_API_KEY="sk-..."
export POSTGRES_URL="postgresql://dibs:dibs@localhost:5432/dibs"
export FRONTEND_ORIGINS="http://localhost:3000"
uvicorn backend.main:app --reload

# Terminal 3 — Celery worker (polling happens here instead of in-process)
celery -A backend.workers.celery_app worker --loglevel=info

# Terminal 4 — frontend
cd frontend
npm install                                    # first run only
npm run dev                                    # http://localhost:3000
```

```powershell
# Terminal 2 on Windows
$env:OPENAI_API_KEY = "sk-..."
$env:POSTGRES_URL = "postgresql://dibs:dibs@localhost:5432/dibs"
$env:FRONTEND_ORIGINS = "http://localhost:3000"
uvicorn backend.main:app --reload
```

Migrations apply themselves at startup. `FRONTEND_ORIGINS` is required or the browser
is blocked by CORS — unset, the API sends no CORS headers at all. To get email instead
of logged notifications, set `SMTP_HOST` and `SMTP_FROM` as well.

Confirm everything bound correctly:

```bash
curl http://localhost:8000/health
# {"watch_store":"redis","watch_queue":"celery","recovery_readiness":"ready", ...}
```

### 4. Everything in Docker

```bash
export OPENAI_API_KEY="sk-..."
docker compose -f infra/docker-compose.yml --profile app up --build
# web → http://localhost:3000    api → http://localhost:8000
```

The `app` profile adds `api`, `worker`, and `web` on top of the datastores. A bare
`up` with no profile starts **only** Redis and PostgreSQL, which is what you want for
mode 3.

Every environment variable and its bounds are documented in
**[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

## Stack

**Backend** FastAPI · Pydantic v2 · Redis (atomic Lua) · PostgreSQL · Celery
**Frontend** Next.js 15 · React 19 · TypeScript strict
**Tests** 55 files, ~15k lines. No test makes a network call; the suite runs the real
Redis Lua through `fakeredis[lua]`, so atomicity is proven against actual script
semantics rather than a hand-written fake.

```bash
pytest && mypy backend
cd frontend && npm run typecheck && npm test
```

Two rules explain most of the design:

- **Degrade, never gate.** Every external dependency is optional, and its absence is a
  defined mode rather than an error. No Redis → in-memory store. No PostgreSQL → no
  projection. No SMTP → notifications log instead of send. No auth header → the
  anonymous path. A *malformed* value still fails startup loudly; only an *absent* one
  degrades.
- **Seams are protocols with two implementations.** The watch repository is in-memory
  *and* Redis-Lua, held to identical behaviour by equivalence tests. Anything with one
  implementation and no second is a claim, not a contract.

## Deploying

**The frontend deploys to Vercel cleanly. The backend does not — and shouldn't.**

The frontend is pure client components calling the API directly over CORS, with no
Next API routes and no server actions, so Vercel is an ideal host: point it at
`frontend/`, set `NEXT_PUBLIC_API_BASE_URL` to your deployed API's public URL, and
deploy. Note that the variable is **inlined at build time**, so changing it requires a
rebuild, not just an environment edit.

The backend is a *stateful, long-running* system and serverless functions are the
wrong shape for it:

- The app's lifespan starts a **background recovery sweep loop** that must keep running
  between requests. A serverless function is frozen or killed the moment it responds.
- **Celery workers are long-lived consumers.** Vercel has no worker runtime.
- **Recovery leader election** assumes replicas that stay alive to hold a lease.
- Polls are paced at **180s ± 30s**, past Vercel's function duration limits.

Run the backend somewhere with real processes — Railway, Render, Fly.io, or any VM —
using the `Dockerfile` in this repo, which runs both the API and the worker from one
image. Pair it with managed Redis and PostgreSQL. Nothing in the code needs to change:
the frontend already talks to the API cross-origin, which is exactly what
`FRONTEND_ORIGINS` exists for.

Deploy ordering matters, because old and new worker code can't safely share one Redis —
see [Deploying a change](docs/ARCHITECTURE.md#deploying-a-change).

## Known limitations

- **No real reservation provider.** The one that matters.
- **Redis Cluster and Sentinel are unsupported** — the atomic Lua assumes every key for
  one watch lives on one node. Standalone, a directly addressed primary, and `rediss://`
  all work.
- **No rolling deploys.** Old and new worker code can't safely share one Redis; see
  the deploy order in the architecture doc.
- **Rate limiting is a spend ceiling, not admission control.** Per-process, keyed on
  client id and origin.

## Docs

- **[Architecture and reference](docs/ARCHITECTURE.md)** — layout, the watch engine, API,
  configuration, health, deploying
- **[docs/specs/](docs/specs/)** — requirements, design, and task packages per milestone

Built as six milestones: AI orchestrator → mock platform adapter → distributed watch
coordinator → write API and frontend → accounts and authentication → notification
delivery.
