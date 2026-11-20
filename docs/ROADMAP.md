# Dibs project roadmap

Dibs will help diners in Kitchener–Waterloo find restaurant tables and act on openings. A diner should be able to describe what they want or fill out a form, track a watch, and receive an email when a table matches. At supported venues, diners will also be able to authorize Dibs to book a matching table automatically.

The watch engine, dashboard, accounts, and email integration already exist. Prompts are read by a free local model through Ollama (OpenAI is optional and off by default), the browser can create a watch from a prompt or a manual form, and the reservation provider is simulated. The first public pilot will focus on real availability alerts for a small set of venues. Real auto-booking follows after the provider integration can safely handle retries and uncertain booking outcomes. See the [current project status](../README.md#status).

## 1. Let Dibs read prompts with Ollama

1. **Set up Ollama on your computer.** On a Mac, install and open Ollama, then download a local model. The README covers [which model to download and how to select it](../README.md#reading-prompts-ollama-by-default-openai-optional). [Ollama Mac setup](https://docs.ollama.com/macos) · [Model download commands](https://docs.ollama.com/cli)
2. **Give Dibs a setting to use Ollama or OpenAI.** When you type “Watch The Bauer Kitchen for four next Saturday between 6 and 9,” either service should send back the same kinds of details in the same format. Dibs then checks those details, asks for anything missing, and handles the watch or booking.
3. **Try the same examples with both.** Test booking, searching, and watching. Try a time range, a date like “next Saturday” in the restaurant’s time zone, a missing party size, and a restaurant name that could match more than one place. If Ollama is offline or returns a response Dibs can’t read, show a clear message and point to the manual form once it’s available.

**Complete when** the example prompts work with Ollama and you can test a simulated watch or booking from the Dibs website without an OpenAI key. [Provider interface](../backend/orchestrator/providers.py)

**Status (Nov 20, 2026): complete.** With `qwen3.5:9b` on Ollama, all eleven example prompts give the expected result in about 2 seconds each: book, search, watch with a time range, "next Saturday" (Dibs asks which one), "tomorrow" in the restaurant's time zone, a missing party size, a venue name that matches two places, a vague "evening", a recreation booking with a duration, and a prompt-injection attempt. If the model is offline or unreadable, the error points to the manual form. Ollama is now the default; OpenAI stays available with `LLM_PROVIDER=openai` but is not used or tested.

## 2. Make watches usable from the browser

- Add a mobile-friendly watch form for venue, date, time or time range, and party size. Submit through the existing `createWatch()` helper so the form works without an LLM.
- Give users a choice between **Notify me** and **Auto-book**. Default to notifications, show the reservation criteria before the user opts into auto-booking, and display the chosen mode on the watch card.
- When a `book` prompt finds no table, offer to create a watch with the parsed details prefilled. Let the user review or edit them and choose a mode. Creating the watch should reuse those details without a second model call.
- Remove the testing message from the no-availability response. Keep simulated confirmations clearly labeled in the browser, dashboard, and any email that describes a mock booking.

**Complete when** someone can create a watch manually with the model offline, turn a no-availability result into a watch, and see an opted-in watch complete a simulated booking in the dashboard. [Watch API helper](../frontend/lib/api.ts)

**Status (Nov 20, 2026): complete.** Checked in Chrome at phone width (390 px) against PostgreSQL. The form fits without sideways scrolling, shows the booking criteria when Auto-book is chosen, and creates the watch without calling the model. That watch made a simulated booking on its first check, and the dashboard shows it as MOCK CONFIRMED · AUTO-BOOK with the simulation label. A long booking ID used to run off the card on a phone; it now wraps. Turning a no-table result into a watch, and sending visitors to the form when the model is offline, are covered by frontend tests.

## 3. Prepare real availability and pilot accounts

### Connect one authorized reservation provider

- Start outreach while building the watch UI. Look for one venue partner or API that authorizes availability checks for a small set of Kitchener–Waterloo venues. Confirm supported time windows, party sizes, request limits, and how diners can finish a booking. Ask about permission to book automatically as part of the later milestone.
- Implement real availability through the existing provider interface. Map venue identifiers and local times, follow provider limits, and handle unavailable or stale results. Include a provider link in the alert so the diner can book directly.
- Keep the mock adapter for local development and demos. Keep the site in clearly labeled demo mode until there is a supported source of real availability.

There are two ways to get a provider that agrees. Pursue both; the first yes wins. Use the [provider checklist](PROVIDER_CHECKLIST.md) for the conversation either way.

**Path A: a booking platform** (the reservation software many restaurants share)

- [ ] List the booking platforms used by Kitchener–Waterloo restaurants: check each venue's "Reserve" button to see which platform it opens.
- [ ] For each platform, look for a partner program or developer API, and note what it asks of applicants.
- [ ] Apply to the most promising one or two, explaining the alert-only pilot: Dibs checks availability and sends diners to the platform to book.
- [ ] Once accepted, get sandbox credentials, the list of covered venues with their IDs, and the request limits.
- [ ] Build the adapter against the sandbox, pass the conformance tests, then switch `RESERVATION_PROVIDER` on for a few venues.

**Path B: a restaurant directly** (one owner who likes the idea)

- [ ] Shortlist three to five local restaurants that often book up, and note which reservation system each uses.
- [ ] Contact the owner or manager with a one-page pitch: Dibs alerts diners when a table opens and sends them to the restaurant's own booking page, which fills cancellations.
- [ ] Ask whether their reservation system can share availability with Dibs: a partner feature, an export, or the system's own API with their permission. Their written permission is required.
- [ ] Agree on limits: which dates, times and party sizes, and how often Dibs may check.
- [ ] Build the adapter for that one restaurant, run the pilot with a few diners, and ask the owner for feedback before adding a second venue.

**Complete when** a watch for a supported venue can detect a real opening and include a working link for the diner to finish booking. [Reservation provider interface](../backend/integrations/base.py)

**Status (Nov 20, 2026): waiting on a provider.** The provider-neutral parts are built: each opening can carry the provider's `https://` booking link, which the alert email, the dashboard, and search results show as **BOOK IT**; and `RESERVATION_PROVIDER` picks the adapter for both the API and the worker. The questions to ask a provider, and what to send once there is one, are in the [provider checklist](PROVIDER_CHECKLIST.md). Still to do with a real provider: the adapter itself, mapping venue IDs and local times, and following its request limits.

### Secure accounts and deliver email alerts

- Move browser sessions from local storage to server-managed cookies with `HttpOnly`, `Secure` in production, and an appropriate `SameSite` policy. Protect cookie-authenticated writes against CSRF and verify signup, login, and logout on the deployed domains.
- Add email verification, password reset with expiring single-use links, and password changes. Test session invalidation when credentials change.
- Let guests create and track watches in the dashboard, and get email alerts by confirming just an email address (no account needed; decided Nov 20, 2026). Signing up still carries their watches into an account through the existing watch-claiming flow. Require a verified account before enabling real auto-booking.

**Complete when** a guest can create a watch, sign up, verify their email, see the same watch on their account, and receive a test alert. Verify password reset and password changes on the deployed site. [Account routes](../backend/api/routes/auth.py)

**Status (Nov 20, 2026): built; final check on the live site is part of step 4.** Guests get alerts by confirming an email address (no account needed). Accounts confirm their email by link, and alerts go only to confirmed addresses. "Forgot password?" emails a single-use link that expires in 1 hour; password changes need the current password; both sign out every other session. Browser sessions are an HttpOnly `SameSite=Lax` cookie, and cookie-authenticated writes must carry a CSRF header. Each piece was tested in Chrome against PostgreSQL. SMS alerts are out of scope for now (per-message billing). On the deployed site: verify sign-up, login, logout, reset and change, and host the frontend and API on the same site so the cookie works.

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
