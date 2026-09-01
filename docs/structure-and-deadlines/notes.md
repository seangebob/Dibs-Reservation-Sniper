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

---

$env:OPENAI_API_KEY = "your-api-key"
.\.venv\Scripts\python.exe -m uvicorn reservation_nlp.api:app --reload
Then submit requests to POST /v1/intents/parse. Setup and example payloads are documented in README.md.

The integration follows OpenAI’s Structured Outputs guide. Content was rephrased for compliance with licensing restrictions.


Option 2: .env file (recommended for local dev only)
Create .env in the project root:


OPENAI_API_KEY=sk-proj-YOUR_KEY_HERE
OPENAI_MODEL=gpt-4o-mini
RESERVATION_TIMEZONE=America/Toronto
Then load it in your app:


from dotenv import load_dotenv
load_dotenv()  # Reads .env file into os.environ

settings = Settings.from_environment()
⚠️ Important: Add .env to .gitignore so it's never committed:


# .gitignore
.env
.env.local
.env.*.local
In Your Code: Using the API Key
Your providers.py already does this correctly:


# src/nlp/providers.py
class OpenAIIntentProvider:
    def __init__(self, *, model: str, api_key: str | None = None, client: Any | None = None):
        if client is None and not api_key:
            raise ValueError("api_key is required when client is not supplied")
        
        self._client = client or AsyncOpenAI(api_key=api_key)  # ← Passes to OpenAI SDK
        self._model = model
Then in your API:


# src/nlp/api.py
settings = Settings.from_environment()
provider = OpenAIIntentProvider(
    api_key=settings.openai_api_key,  # ← Never hardcode
    model=settings.openai_model,
)
Production Setup
Heroku / Cloud Deployment
Set environment variables via the platform's secrets manager:


# Heroku
heroku config:set OPENAI_API_KEY=sk-proj-YOUR_KEY_HERE

# AWS Lambda
aws lambda update-function-configuration \
  --function-name my-reservation-app \
  --environment Variables={OPENAI_API_KEY=sk-proj-...}

# Docker
docker run -e OPENAI_API_KEY=sk-proj-... my-app
Docker Compose

# docker-compose.yml
services:
  api:
    image: my-reservation-app
    environment:
      OPENAI_API_KEY: ${OPENAI_API_KEY}  # Read from host OS env
      OPENAI_MODEL: gpt-4o-mini
      RESERVATION_TIMEZONE: America/Toronto
Run with:


OPENAI_API_KEY=sk-proj-... docker-compose up
Complete Setup Checklist

# 1. Get an API key from OpenAI dashboard
# https://platform.openai.com/api/keys → "Create new secret key"

# 2. Set it in your shell (local dev)
$env:OPENAI_API_KEY = "sk-proj-YOUR_KEY_HERE"

# 3. Verify it's set
echo $env:OPENAI_API_KEY  # Should show your key (masked on some systems)

# 4. Run your app
.\.venv\Scripts\python.exe -m uvicorn reservation_nlp.api:app --reload

# 5. Test the endpoint
curl -X POST http://localhost:8000/v1/intents/parse \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Book Cote for 4 next Saturday at 7pm"}'