# Dibs, a sniper for reservations.
*reserves different restaurants + recreational places in KW area by just one prompt*

Below is the high-level system architecture outlining how user requests flow from the front-end website through the AI orchestration engine and down to execution or clarification paths.

```
┌─────────────────────────────────────────────────────────────┐
│                      User via Website                       │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│             API Gateway (Auth, Rate Limiting)               │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                  AI Orchestration Engine                    │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                    Booking Reservations                     │
└──────────────┬───────────────────────────────┬──────────────┘
               │                               │
       [ Function Call ]               [ Missing Info ]
               │                               │
               ▼                               ▼
┌──────────────────────────────┐┌──────────────────────────────┐
│  Booking, Search, Database   ││ Clarify details with user    │
│            Writes            │└──────────────────────────────┘
└──────────────┬───────────────┘
               │
               ▼
┌────────────────────────────────┐
│  Background Workers / Polling  │
└────────────────────────────────┘

```

--- 

System Design Breakdown:
1. Client & Presentation Layer
    - Captures unstructured input from user (text-to-speech, raggy text) and renders system status updates
        - key design: keep business logic out of control on front end. sends raw strings to backend (e.g., "Set a watch for 4 people at Cote next Saturday night") and receives a structured and user-friendly status back.

2. Gate & Security Layer
    - manages authentication, rate limiting, and input sanitization
        - prevents API abuse and ensuring malicuious user input (prompt injections trying to override system) that can be intercepted before touching billing quotas or databases.

3. AI Orchestration Engine
    - structures regex parsers or NLP tokenizers with LLM using structured JSON mode / outputs

    Execution flow:
    - user's prompt is combined with system guidelines and relevant session context
    - LLM evaluates prompt against defined functions
    - model returns strict, machine-readable JSON rather than free-form text

```json
{
  "action": "CREATE_MONITOR",
  "parameters": {
    "venue_name": "Cote",
    "party_size": 4,
    "date": "2026-08-22",
    "time_range": ["18:00", "21:00"]
  }
}
```
    Guardrails and fallback
    - if mandatory parameters are missing (e.g., did not mention paraity size), the engine catches validation failure and asks targetted follow-up question instead of of calling downstream servicesz

4. Execution + Business Logic Layer
    Role:
        - Takes validated JSON parameters from AI Orchestrator and executes real application logic
    Synchronous Actions:
        - Immediate tasks (querying current database records or authenticating a user) runs directly via backend services
    Asynchronus Actions:
        - Long-running tasks (e.g, 24/7 resevation, monitoring, sending emails, or batch scraping) are pushed to background task queue (e.g, Redis + Celery / BullMQ (wgat))


5. Persistence Layer
    - Database (PostgreSQL / MongoDB): Stores user profiles, past conversation logs, structured entity requests, and system stakes
    - Redis Cache & Queue, Manages background job scheduling with backoff/jitter strategies to execute tasks smoothly without getting rate-limited by third-party services

---

## Milestone 1: Natural-language parser

The first milestone is implemented as an isolated FastAPI service under `src/reservation_nlp`. It accepts raw user text, resolves relative dates in the `America/Toronto` timezone, and returns exactly five validated fields. Missing or ambiguous values remain `null` and produce one targeted question; no booking or search is executed at this stage.

### Setup (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
$env:OPENAI_API_KEY = "your-api-key"
$env:OPENAI_MODEL = "gpt-5.6"
.\.venv\Scripts\python.exe -m uvicorn reservation_nlp.api:app --reload
```

Run the server command manually because it remains active until stopped. Configuration variables are documented in `.env.example`; the application does not automatically load `.env` files or commit secrets.

### API contract

`POST /v1/intents/parse`

```json
{
  "prompt": "Book Cote for four next Saturday at 7 pm"
}
```

Complete response:

```json
{
  "restaurant": "Cote",
  "party_size": 4,
  "date": "2026-08-22",
  "preferred_time": "19:00",
  "missing_info": null
}
```

Clarification response:

```json
{
  "restaurant": "Cote",
  "party_size": null,
  "date": "2026-08-22",
  "preferred_time": "19:00",
  "missing_info": "How many people are in your party?"
}
```

Run deterministic tests without making model API calls:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

The provider uses OpenAI's Responses API with native Pydantic structured output, following the [official Structured Outputs guide](https://platform.openai.com/docs/guides/structured-outputs). Content from that guide was rephrased for compliance with licensing restrictions.
