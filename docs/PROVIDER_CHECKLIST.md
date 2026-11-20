# Reservation provider checklist

Use this when talking to a venue or reservation platform about letting Dibs check
availability (roadmap [step 3a](ROADMAP.md#connect-one-authorized-reservation-provider)).
The first pilot only needs **availability and a booking link**. Automatic booking
comes later (step 5), so ask about it now but don't wait on it.

## What Dibs needs for the alert pilot

| Need | Why |
| --- | --- |
| Written permission to check availability automatically | Dibs checks on a schedule, not on a person's click. Scraping without permission is off the table. |
| A way to ask: venue, date, party size, time or time range → open tables | This is the one call every watch makes. |
| A stable ID for each venue | Dibs matches its own venue names ("The Bauer Kitchen") to the provider's IDs. |
| For each open table: start time, and the largest party it seats | Dibs only alerts on tables that fit the request. |
| A link where the diner finishes the booking | Alerts and the dashboard show **BOOK IT** with this link. It must be `https://`. |
| Their request limits | Dibs checks every 3 minutes per watch by default and can slow down. |
| A test account or sandbox | So the connector can be built and tested without touching real tables. |

## Questions to ask

1. Which Kitchener–Waterloo venues could we start with? Five to ten is plenty.
2. How do we get access: API key, partner agreement, terms to accept?
3. How many requests per minute or per day may we make? What happens if we go over?
4. Which party sizes, dates (how far ahead), and times can we ask about?
5. Is there a booking link we can send diners to, ideally with the date, time and party size already filled in?
6. Is there a test or sandbox environment?
7. Do you send notices when a table opens (a webhook), or do we have to ask?
8. **For later (automatic booking):**
   - Are we allowed to book on a diner's behalf?
   - If our booking request times out, can we ask afterwards whether it went through?
   - Can a repeated request be recognized so it never books twice?
   - How are bookings cancelled, and what are the venue's cancellation and no-show terms?

## What to send me once you have a provider

- Their API documentation, or the name of the platform and a link to its developer docs.
- Test credentials. Share them privately and never commit them to the repo.
- The list of venues they cover, with their IDs.
- Their request limits.

With those, the connector plugs into the existing adapter interface
([base.py](../backend/integrations/base.py)). It must pass the same conformance tests
as the simulation ([test_adapter_conformance.py](../tests/test_adapter_conformance.py)),
and it is switched on with `RESERVATION_PROVIDER` ([registry.py](../backend/integrations/registry.py)).
The site stays labeled as a demo until that happens.
