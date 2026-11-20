# Dibs, A Reservation Sniper

**A reservation sniper for Kitchener–Waterloo.** Describe what you want in plain
English — *"watch A Restaurant for four next Saturday between 6 and 9"* — and Dibs
opens a durable background watch, polls for availability on a jittered cadence, books
the moment a table appears, and emails you.

> [!IMPORTANT]
> **Dibs books against a built-in mock provider, not a real one.** Everything below
> the provider seam is production-grade — distributed coordination, fenced claims,
> crash recovery, accounts, email — but no real venue is ever contacted. A booking
> returns status `MOCK_CONFIRMED`, which by design can never be mistaken for a table
> a venue is actually holding. It is currently a work in progress, as within everything
> else within this project as well.

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
| Intent extraction and validation | **Real** — a free local model via Ollama by default; OpenAI optional |
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

**Environment variables.** The commands below `export` what they need. To keep them in
a file instead, `cp .env.example .env`, fill it in, and load it in every terminal that
runs the API or the worker — nothing reads `.env` automatically:

```bash
set -a; source .env; set +a
```

### 1. Minimal — API only, no infrastructure

With no Redis and no PostgreSQL the app boots on an in-memory store and an in-process
queue. Watches, polling, and booking all work; nothing survives a restart. Two things
need PostgreSQL (mode 3): accounts (`/api/auth/*` returns `503`) and the `/watches`
dashboard, which reads `/api/watches/mine` and stays empty without it.

```bash
git clone https://github.com/seangebob/reservation-sniper.git
cd reservation-sniper
python -m venv .venv
```

The install pulls in every extra — `worker` (Celery, needed for mode 3), `test`, and
`dev` (mypy) — so one install covers every mode below. `pip install -r requirements.txt`
installs the same set minus mypy.

```bash
# macOS / Linux
source .venv/bin/activate
pip install -e ".[worker,test,dev]"
uvicorn backend.main:app --reload
```

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
pip install -e ".[worker,test,dev]"
uvicorn backend.main:app --reload
```

Open <http://localhost:8000/docs> for the interactive API, or
<http://localhost:8000/health> to see which store and queue actually got bound.

Prompts are read by a free model on your own machine through Ollama — install it and
download the model first; see [Reading prompts](#reading-prompts-ollama-by-default-openai-optional).

Without Ollama running the server still starts — only the two prompt endpoints
return `503`. Everything else, including creating watches through
`POST /api/watches`, works without it:

```bash
curl -X POST "http://localhost:8000/api/watches?auto_book=true" \
  -H "Content-Type: application/json" \
  -d '{
        "venue_name": "The Bauer Kitchen",
        "venue_type": "RESTAURANT",
        "market": "Kitchener-Waterloo-Cambridge, ON",
        "party_size": 4,
        "date": "2026-12-05",
        "preferred_time": "19:00",
        "time_window": null,
        "duration_minutes": null,
        "special_requests": []
      }'
```

Every field is required: send `null` or `[]` for the ones you don't need, `market` must
be exactly that string, and `date` must be today or later. `auto_book=true` books the
first matching slot; without it the watch only reports what it finds. Follow it with
`GET /api/watches/{watch_id}`, or use **Try it out** on `/docs` instead of curl.

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
Every new terminal starts with an empty environment, so activate the venv and export
the variables in **each** Python terminal (2 and 3).

```bash
# Terminal 1 — Redis + PostgreSQL
docker compose -f infra/docker-compose.yml up -d

# Terminal 2 — API
source .venv/bin/activate
export POSTGRES_URL="postgresql://dibs:dibs@localhost:5432/dibs"
uvicorn backend.main:app --reload

# Terminal 3 — Celery worker (polling happens here instead of in-process)
source .venv/bin/activate
export POSTGRES_URL="postgresql://dibs:dibs@localhost:5432/dibs"
celery -A backend.workers.celery_app worker --loglevel=info

# Terminal 4 — frontend
cd frontend
npm install                                    # first run only
npm run dev                                    # http://localhost:3000
```

```powershell
# Terminal 2 on Windows
.\.venv\Scripts\Activate.ps1
$env:POSTGRES_URL = "postgresql://dibs:dibs@localhost:5432/dibs"
uvicorn backend.main:app --reload

# Terminal 3 on Windows — Celery's default pool doesn't run on Windows; use solo
.\.venv\Scripts\Activate.ps1
$env:POSTGRES_URL = "postgresql://dibs:dibs@localhost:5432/dibs"
celery -A backend.workers.celery_app worker --loglevel=info --pool=solo
```

The worker needs `POSTGRES_URL` too: it does the polling and booking, so without it
what it finds never reaches the `/watches` dashboard, and no email goes out — the
dashboard shows the watch stuck at whatever the API last recorded.

Migrations apply themselves at startup. `npm run dev` forwards the website's `/api`
requests to the API at `localhost:8000`, so the browser never calls the API
cross-origin and `FRONTEND_ORIGINS` isn't needed. It is needed when the browser calls
the API directly — Docker (mode 4), a deployment, or `NEXT_PUBLIC_API_BASE_URL` set —
because unset, the API sends no CORS headers at all.

To get email instead of logged notifications, set `SMTP_HOST` and `SMTP_FROM` (plus
`SMTP_USERNAME`/`SMTP_PASSWORD` if your relay needs them) in **both** terminal 2 and
terminal 3. Email goes to signed-in accounts only; anonymous watches have no address
to send to.

Confirm everything bound correctly:

```bash
curl http://localhost:8000/health
# {"watch_store":"redis","watch_queue":"celery","recovery_readiness":"ready", ...}
```

### 4. Everything in Docker

```bash
docker compose -f infra/docker-compose.yml --profile app up --build
# web → http://localhost:3000    api → http://localhost:8000
```

The `app` profile adds `api`, `worker`, and `web` on top of the datastores. A bare
`up` with no profile starts **only** Redis and PostgreSQL, which is what you want for
mode 3.

Compose takes the `SMTP_*` settings and the rest from your shell.
It does **not** read the repo-root `.env` on its own (it only looks in `infra/`); to
use that file, pass it explicitly:

```bash
docker compose --env-file .env -f infra/docker-compose.yml --profile app up --build
```

### Reading prompts: Ollama by default, OpenAI optional

Prompts are read by a model running on your own machine: free, and nothing leaves
your computer. It takes about 2–3 seconds per prompt and a one-time download of about
8 GB. Any server with an OpenAI-compatible Chat Completions API works — Ollama, LM
Studio, llama.cpp, vLLM. With [Ollama](https://ollama.com/download):

```bash
# once, after installing Ollama
ollama pull qwen3.5:9b
```

Keep the Ollama app running; the API needs no setting for it. It logs
`Prompts are read by local model 'qwen3.5:9b' at http://localhost:11434/v1` at startup.
For another model or server, set `LOCAL_LLM_MODEL` and `LOCAL_LLM_BASE_URL` (LM Studio
serves `http://localhost:1234/v1`). Only the API reads prompts; the worker needs none
of this.

In Docker (mode 4), Ollama keeps running on your machine and the `api` container
reaches it at `host.docker.internal`, which is the compose default. On Linux, start
Ollama with `OLLAMA_HOST=0.0.0.0` so the container can connect.

To use OpenAI instead, set `LLM_PROVIDER=openai` and `OPENAI_API_KEY` (a key with
credit) for the API; `OPENAI_MODEL` defaults to `gpt-4o-mini`. A key on its own does
nothing: Dibs uses OpenAI only when `LLM_PROVIDER` names it.

If the server isn't running or the model hasn't finished downloading, the prompt box
says the assistant is temporarily unavailable and the API log says why — for a
missing model, the exact `ollama pull` to run. Every reply goes through the same
validation as OpenAI's, and malformed output gets one corrective retry before it is
reported as an error. A small model can still misread a detail such as a relative
date, so check the parsed details before trusting a booking.

Every environment variable and its bounds are documented in
**[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

## Stack

- **Backend:** FastAPI, Pydantic v2, Redis (atomic Lua), PostgreSQL, Celery
- **Frontend:** Next.js 15, React 19, TypeScript strict
- **Tests:** 55 files, ~15k lines. No test makes a network call; the suite runs the real
Redis Lua through `fakeredis[lua]`, so atomicity is proven against actual script
semantics rather than a hand-written fake.

```bash
pytest && mypy                    # mypy reads its target (backend/) from pyproject.toml
cd frontend && npm ci && npm run typecheck && npm test
```

Two rules explain most of the design:

- **Degrade, never gate.** Every external dependency is optional, and its absence is a
  defined mode rather than an error. No Redis → in-memory store. No PostgreSQL → no
  projection. No SMTP → notifications log instead of send. No auth header → the
  anonymous path. A *malformed* value is never quietly treated as absent: the API still
  boots, but logs a `ConfigurationError` and switches off whatever depends on that
  value (a bad watch setting makes the watch routes return `503`; a bad
  `POSTGRES_URL`, `FRONTEND_ORIGINS`, or SMTP setting leaves that feature off). Check
  the startup log when something you configured isn't working.
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
- **Rate limiting is a spend ceiling, not admission control.** Limits are shared
  through Redis when it is connected (per-process otherwise). Behind a hosting proxy,
  set `FORWARDED_ALLOW_IPS` so limits see real client addresses.

## Docs

- **[Project roadmap](docs/ROADMAP.md)** — implementation milestones from Ollama through
  a real availability pilot and auto-booking
- **[Provider checklist](docs/PROVIDER_CHECKLIST.md)** — what to ask a reservation
  provider, and what to send once there is one
- **[Architecture and reference](docs/ARCHITECTURE.md)** — layout, the watch engine, API,
  configuration, health, deploying
- **[docs/specs/](docs/specs/)** — requirements, design, and task packages per milestone

Built as six milestones: AI orchestrator → mock platform adapter → distributed watch
coordinator → write API and frontend → accounts and authentication → notification
delivery.
