Milestone 1 complete
Implemented a production-ready NLP foundation with:

POST /v1/intents/parse FastAPI endpoint
Strict five-field ReservationIntent output
OpenAI native Pydantic structured outputs
Missing/ambiguous information clarification
Relative date handling using America/Toronto
Deterministic rejection of past reservation dates
Prompt-injection separation between system instructions and user input
Shared, concurrency-safe OpenAI client with shutdown cleanup
Provider errors mapped to HTTP 502; configuration errors to 503
Environment-only API key configuration
Pinned dependencies and PowerShell setup instructions
Key implementation files are under src/reservation_nlp/, with tests under tests/.

Validation
15 tests passed
Python compilation passed
OpenAI strict JSON Schema compatibility passed
API health smoke test passed
Missing API key behavior returns 503
git diff --check passed
A live OpenAI request was not made because no API key was supplied, avoiding charges. To run manually:

powershell

$env:OPENAI_API_KEY = "your-api-key"
.\.venv\Scripts\python.exe -m uvicorn reservation_nlp.api:app --reload
Then submit requests to POST /v1/intents/parse. Setup and example payloads are documented in README.md.

The integration follows OpenAI’s Structured Outputs guide. Content was rephrased for compliance with licensing restrictions.