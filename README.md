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

## Quickstart

Redis and PostgreSQL are upgrades, not requirements — with neither, the app boots on
an in-memory store and an in-process queue.

```bash
python -m venv .venv
.venv/bin/pip install -e ".[test]"       # Windows: .\.venv\Scripts\pip.exe
export OPENAI_API_KEY="sk-..."           # Windows: $env:OPENAI_API_KEY = "sk-..."
uvicorn backend.main:app --reload
```

See the whole watch loop with nothing running at all:

```bash
PYTHONPATH=. python misc/scripts/watch_demo.py
```

Full stack in Docker — API, worker, frontend, Redis, PostgreSQL:

```bash
export OPENAI_API_KEY="sk-..."
docker compose -f infra/docker-compose.yml --profile app up --build
# web → http://localhost:3000    api → http://localhost:8000
```

Setup details, every environment variable, and the API reference are in
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
