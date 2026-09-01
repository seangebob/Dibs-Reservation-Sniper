# Milestone 3: Set Up Background Queue + State
- Set up event-driven task queue instead

1. Start local Redis instance via Docker (`docker run -p 6739:6739 redis`)
2. Implement Celery (Python) or BullMQ (Node.js or TypeScript) for background jobs
3. Write simpler queue handler that accepts a watch request and executes polling check every few minutes with a randomized jitter (around 30 secs) to emulate human behaviour

HAVE THIS DONE 9/3/26