# Dibs project roadmap

Dibs will help diners in Kitchener–Waterloo find restaurant tables and act on openings. A diner should be able to describe what they want or fill out a form, track a watch, and receive an email when a table matches. At supported venues, diners will also be able to authorize Dibs to book a matching table automatically.

The watch engine, dashboard, accounts, and email integration already exist. Prompts can be read by OpenAI or by a free local model through Ollama, the browser can create a watch from a prompt or a manual form, and the reservation provider is simulated. The first public pilot will focus on real availability alerts for a small set of venues. Real auto-booking follows after the provider integration can safely handle retries and uncertain booking outcomes. See the [current project status](../README.md#status).

## 1. Let Dibs read prompts with Ollama

1. **Set up Ollama on your computer.** On a Mac, install and open Ollama, then download a local model. The README covers [which model to download and how to select it](../README.md#reading-prompts-with-a-local-model-no-openai-key). [Ollama Mac setup](https://docs.ollama.com/macos) · [Model download commands](https://docs.ollama.com/cli)
2. **Give Dibs a setting to use Ollama or OpenAI.** When you type “Watch The Bauer Kitchen for four next Saturday between 6 and 9,” either service should send back the same kinds of details in the same format. Dibs then checks those details, asks for anything missing, and handles the watch or booking.
3. **Try the same examples with both.** Test booking, searching, and watching. Try a time range, a date like “next Saturday” in the restaurant’s time zone, a missing party size, and a restaurant name that could match more than one place. If Ollama is offline or returns a response Dibs can’t read, show a clear message and point to the manual form once it’s available.

**Complete when** the example prompts work with both options and you can test a simulated watch or booking from the Dibs website with Ollama and without an OpenAI key. [Provider interface](../backend/orchestrator/providers.py)

**Status (Nov 20, 2026):** With `qwen3.5:9b` on Ollama, all eleven example prompts give the expected result in about 2 seconds each: book, search, watch with a time range, "next Saturday" (Dibs asks which one), "tomorrow" in the restaurant's time zone, a missing party size, a venue name that matches two places, a vague "evening", a recreation booking with a duration, and a prompt-injection attempt. If the model is offline or unreadable, the error points to the manual form. Still to do: run the same prompts with OpenAI, which needs a working API key.

## 2. Make watches usable from the browser

- Add a mobile-friendly watch form for venue, date, time or time range, and party size. Submit through the existing `createWatch()` helper so the form works without an LLM.
- Give users a choice between **Notify me** and **Auto-book**. Default to notifications, show the reservation criteria before the user opts into auto-booking, and display the chosen mode on the watch card.
- When a `book` prompt finds no table, offer to create a watch with the parsed details prefilled. Let the user review or edit them and choose a mode. Creating the watch should reuse those details without a second model call.
- Remove the testing message from the no-availability response. Keep simulated confirmations clearly labeled in the browser, dashboard, and any email that describes a mock booking.

**Complete when** someone can create a watch manually with the model offline, turn a no-availability result into a watch, and see an opted-in watch complete a simulated booking in the dashboard. [Watch API helper](../frontend/lib/api.ts)

## 3. Prepare real availability and pilot accounts

### Connect one authorized reservation provider

- Start outreach while building the watch UI. Look for one venue partner or API that authorizes availability checks for a small set of Kitchener–Waterloo venues. Confirm supported time windows, party sizes, request limits, and how diners can finish a booking. Ask about permission to book automatically as part of the later milestone.
- Implement real availability through the existing provider interface. Map venue identifiers and local times, follow provider limits, and handle unavailable or stale results. Include a provider link in the alert so the diner can book directly.
- Keep the mock adapter for local development and demos. Keep the site in clearly labeled demo mode until there is a supported source of real availability.

**Complete when** a watch for a supported venue can detect a real opening and include a working link for the diner to finish booking. [Reservation provider interface](../backend/integrations/base.py)

### Secure accounts and deliver email alerts

- Move browser sessions from local storage to server-managed cookies with `HttpOnly`, `Secure` in production, and an appropriate `SameSite` policy. Protect cookie-authenticated writes against CSRF and verify signup, login, and logout on the deployed domains.
- Add email verification, password reset with expiring single-use links, and password changes. Test session invalidation when credentials change.
- Let guests create and track watches in the dashboard. Explain that they can sign up and verify their email to receive alerts, and use the existing watch-claiming flow to carry the watch into their account. Require a verified account before enabling real auto-booking.

**Complete when** a guest can create a watch, sign up, verify their email, see the same watch on their account, and receive a test alert. Verify password reset and password changes on the deployed site. [Account routes](../backend/api/routes/auth.py)

## 4. Launch an invite-only alert pilot

- Deploy the frontend, API, long-running watch worker, Redis, PostgreSQL, and email relay. Run Ollama on backend infrastructure so visitors can use the prompt interface from a browser without installing a model. Verify prompt availability and response times under pilot traffic.
- Add metrics and alerts for overdue watch checks, provider failures, model errors, and failed email sends. Verify that watches survive a worker restart and that the deployment reports an unhealthy dependency when a required pilot service is unavailable.
- Test the entire flow on mobile and desktop. Explain that Dibs checks periodically and that availability can change before the diner completes a booking. Keep real booking writes disabled during the alert pilot.

**Complete when** an invited diner can create a watch on the hosted site, receive an email for a real opening, and follow the link to book a table. The operator can detect a stalled worker or an email failure. [Deployment guide](../README.md#deploying)

## 5. Enable real auto-booking at supported venues

- Use a provider that authorizes automated bookings and can confirm what happened after a timeout or interrupted request. Extend the integration to submit bookings, reconcile uncertain outcomes, and keep retries from creating duplicates.
- Show the venue, date, time window, party size, and applicable booking terms before a user opts in. Give the diner a provider confirmation and clear cancellation instructions. State whether an action stops future watch checks or cancels a confirmed reservation.
- Test an opening disappearing before booking, a timeout after the provider accepts the request, duplicate job delivery, a worker restart, and cancellation during a booking attempt. Surface uncertain outcomes as pending and alert the operator when they need attention. Roll out to pilot users and venues with a way to disable new booking attempts.

**Complete when** an opted-in watch can make one real reservation, display and email the provider confirmation, and recover from retries without duplicating the reservation. The alert-only pilot can continue while authorized booking access is pending. [Booking and reconciliation interface](../backend/integrations/base.py)

## Maintenance and expansion

- Add ESLint and run it alongside frontend type checks and tests in CI.
- Keep Ontario holiday coverage ahead of the supported booking window. Generate coverage for the requested year or extend the maintained range before its current 2031 cutoff affects valid requests. [Holiday calendar](../backend/data/venues.py)
- Use the pilot’s watch, provider, and email metrics to decide when to expand venue coverage and invite more users.
