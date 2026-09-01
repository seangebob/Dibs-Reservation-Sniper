Milestone 4: Write API and Front-End

Once Orchestration works end-to-end via script, build UI interface
- Next.js or FASTAPI + React frontend with input or natural language request
- Direct user input `/api/parse-and-book` endpoint
- If parameters are complete, execute an adapter check
    - if slots are unavailable, automatically persist active watch in PostgreSQL and dispatch Redis background task