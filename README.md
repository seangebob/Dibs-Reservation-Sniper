# Reservation (NO NAME)
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