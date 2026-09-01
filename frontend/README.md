# Dibs Web

The Next.js frontend for Dibs (Milestone 4). Talks to the FastAPI backend over
HTTP; it is additive — the backend runs standalone without it.

## Local development

Requires Node 20+ (developed on Node 24). From `apps/web`:

```bash
npm install
cp .env.local.example .env.local   # points at http://localhost:8000 by default
npm run dev                        # http://localhost:3000
```

Run the backend separately so the browser has something to call, and allow the
frontend origin through CORS:

```bash
# from the repo root, in another terminal
FRONTEND_ORIGINS=http://localhost:3000 uvicorn backend.main:app --reload
```

## Scripts

| Command             | What it does                                  |
| ------------------- | --------------------------------------------- |
| `npm run dev`       | Dev server with hot reload (long-running)     |
| `npm run build`     | Production build                              |
| `npm run start`     | Serve the production build (long-running)     |
| `npm run typecheck` | `tsc --noEmit`                                |
| `npm run test`      | One-shot Vitest run                          |

## Structure

```text
apps/web/
├── app/
│   ├── layout.tsx        # Root layout
│   └── page.tsx          # Prompt page (placeholder until Task 9)
├── lib/
│   ├── client-id.ts      # Anonymous X-Dibs-Client-Id (localStorage)
│   └── api.ts            # Typed fetch wrapper, normalizes every failure
└── types/
    └── api.ts            # Hand-maintained mirror of the backend contracts
```
