# Dibs Web

The Next.js frontend for Dibs. It is pure client-side code calling the FastAPI
backend directly over CORS — no Next API routes, no server actions — so the
backend runs fine without it, and it deploys anywhere that serves Next.js.

## Local development

Requires Node 20+ (CI and the Docker image use Node 22). From `frontend/`:

```bash
npm install
cp .env.local.example .env.local   # points at http://localhost:8000 by default
npm run dev                        # http://localhost:3000
```

Run the backend separately so the browser has something to call, and allow the
frontend origin through CORS — without `FRONTEND_ORIGINS` the browser blocks
every request:

```bash
# from the repo root, in another terminal (venv activated)
FRONTEND_ORIGINS=http://localhost:3000 uvicorn backend.main:app --reload
```

What works depends on the backend's setup (see the root README):

- **The prompt box** needs `OPENAI_API_KEY` on the backend; without it, every
  prompt shows "OPENAI_API_KEY is not configured".
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
│   ├── page.tsx                # Prompt page (/)
│   ├── watches/page.tsx        # Watch dashboard (/watches)
│   ├── account/page.tsx        # Sign in / sign up (/account)
│   ├── globals.css             # "Night Scope" theme
│   └── _components/
│       ├── PromptConsole.tsx   # Prompt input + request state machine
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
│   └── format.ts               # Display helpers
└── types/
    └── api.ts                  # Hand-maintained mirror of the backend contracts
```

Tests sit next to the code they cover (`*.test.ts` / `*.test.tsx`).
