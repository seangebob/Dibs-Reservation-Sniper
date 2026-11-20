# Dibs Web

The Next.js frontend for Dibs. It is pure client-side code calling the FastAPI
backend — no Next API routes, no server actions — so the backend runs fine
without it, and it deploys anywhere that serves Next.js. Deployed builds call the
API directly over CORS; the dev server forwards to it instead (below).

## Local development

Requires Node 20+ (CI and the Docker image use Node 22). From `frontend/`:

```bash
npm install
npm run dev                        # http://localhost:3000
```

Run the backend separately so the browser has something to call:

```bash
# from the repo root, in another terminal (venv activated)
uvicorn backend.main:app --reload
```

The dev server forwards `/api/*` to the backend at `http://localhost:8000`, so the
browser only ever talks to its own origin: no CORS setup, and a phone on the same
Wi-Fi can open `http://<this computer's IP>:3000` and reach the API through it.
To call a backend somewhere else instead, set `NEXT_PUBLIC_API_BASE_URL` (see
`.env.local.example`); the browser then calls it directly, and that backend needs
`FRONTEND_ORIGINS` set to this site's origin.

What works depends on the backend's setup (see the root README):

- **The prompt box** needs a model on the backend — `OPENAI_API_KEY`, or a local
  model with `LLM_PROVIDER=local`; without either, every prompt shows
  "OPENAI_API_KEY is not configured".
- **The "Fill in a form" tab** needs no model at all: it posts the watch straight
  to `POST /api/watches`.
- **The watch dashboard and sign-in** need `POSTGRES_URL`; without it the
  dashboard is always empty and sign-in shows "Accounts are unavailable because
  PostgreSQL is not configured".

`NEXT_PUBLIC_API_BASE_URL` is inlined at build time, so changing it needs a
rebuild (or a dev-server restart), not just an environment edit.

## Scripts

| Command             | What it does                                  |
| ------------------- | --------------------------------------------- |
| `npm run dev`       | Dev server with hot reload (long-running)     |
| `npm run build`     | Production build (also writes `.next/standalone` for Docker) |
| `npm run start`     | Serve the production build (long-running)     |
| `npm run typecheck` | `tsc --noEmit`                                |
| `npm test`          | One-shot Vitest run                           |
| `npm run test:watch`| Vitest in watch mode                          |

## Structure

```text
frontend/
├── app/
│   ├── layout.tsx              # Root layout: fonts, AuthProvider
│   ├── page.tsx                # Front door (/): prompt or form
│   ├── watches/page.tsx        # Watch dashboard (/watches)
│   ├── account/page.tsx        # Sign in / sign up (/account)
│   ├── globals.css             # "Night Scope" theme
│   └── _components/
│       ├── EntryPanel.tsx      # "Describe it" / "Fill in a form" tabs
│       ├── PromptConsole.tsx   # Prompt input + request state machine
│       ├── WatchForm.tsx       # Manual watch form, notify or auto-book
│       ├── ResultView.tsx      # Renders each prompt outcome
│       ├── WatchDashboard.tsx  # Loads, refreshes, and cancels your watches
│       ├── WatchCard.tsx       # One watch
│       ├── AuthProvider.tsx    # Signed-in account context
│       ├── AuthForm.tsx        # Sign-in / sign-up form
│       ├── AccountBadge.tsx    # Account corner of the masthead
│       ├── Masthead.tsx        # Top bar and nav
│       └── Scope.tsx           # Decorative SVG backdrop
├── lib/
│   ├── api.ts                  # Typed fetch wrapper; normalizes every failure
│   ├── auth.ts                 # Session token store
│   ├── client-id.ts            # Anonymous X-Dibs-Client-Id
│   ├── format.ts               # Display helpers
│   └── watch-form.ts           # Form values ⇄ watch query, validation
└── types/
    └── api.ts                  # Hand-maintained mirror of the backend contracts
```

Tests sit next to the code they cover (`*.test.ts` / `*.test.tsx`).
