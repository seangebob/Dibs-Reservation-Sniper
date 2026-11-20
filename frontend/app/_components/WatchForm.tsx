"use client";

/**
 * Open a watch by filling in the details — no language model involved. It
 * posts straight to `POST /api/watches` through `createWatch()`, so it works
 * whether or not prompt parsing is configured.
 *
 * Notify is the default. Choosing auto-book first shows exactly what will be
 * booked, so nobody opts in without seeing the criteria.
 */

import { useEffect, useId, useState } from "react";
import { createWatch, listVenues } from "@/lib/api";
import { formatDate, formatTimeBand } from "@/lib/format";
import {
  emptyForm,
  matchVenue,
  toQuery,
  venueToday,
  type WatchFormValues,
} from "@/lib/watch-form";
import type { VenueSummary, VenueType, Watch } from "@/types/api";
import { GuestAlerts } from "./GuestAlerts";

type Phase =
  | { phase: "editing"; error: string | null }
  | { phase: "submitting" }
  | { phase: "created"; watch: Watch };

const VENUE_TYPES: { value: VenueType; label: string }[] = [
  { value: "RESTAURANT", label: "Restaurant or café" },
  { value: "RECREATION", label: "Activity or recreation" },
  { value: "UNKNOWN", label: "Not sure" },
];

export function WatchForm({
  initial,
  submitLabel,
  allowAnother = true,
}: {
  /** Prefilled values, e.g. from a prompt that found no table. */
  initial?: WatchFormValues;
  /** Overrides the notify-mode submit label. */
  submitLabel?: string;
  /** Offer "set up another" after a watch is created. */
  allowAnother?: boolean;
}) {
  const id = useId();
  const [values, setValues] = useState<WatchFormValues>(() => initial ?? emptyForm());
  const [autoBook, setAutoBook] = useState(false);
  const [venues, setVenues] = useState<VenueSummary[]>([]);
  const [state, setState] = useState<Phase>({ phase: "editing", error: null });
  const today = venueToday();

  useEffect(() => {
    let live = true;
    void listVenues().then((result) => {
      // Suggestions are a convenience: on failure the form still works.
      if (live && result.ok) setVenues(result.data);
    });
    return () => {
      live = false;
    };
  }, []);

  const known = matchVenue(values.venueName, venues);
  const busy = state.phase === "submitting";

  function update<K extends keyof WatchFormValues>(key: K, value: WatchFormValues[K]) {
    setValues((current) => ({ ...current, [key]: value }));
    if (state.phase === "editing" && state.error) {
      setState({ phase: "editing", error: null });
    }
  }

  function onVenueChange(name: string) {
    const match = matchVenue(name, venues);
    setValues((current) => ({
      ...current,
      venueName: name,
      venueType: match ? match.venue_type : current.venueType,
    }));
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    // A catalog alias ("bauer") is sent as the venue's proper name, so the
    // watch card and any email name the place the criteria box showed.
    const check = toQuery(
      { ...values, venueName: known?.name ?? values.venueName },
      today,
    );
    if (!check.ok) {
      setState({ phase: "editing", error: check.error });
      return;
    }
    setState({ phase: "submitting" });
    const result = await createWatch(check.query, autoBook);
    if (result.ok) {
      setState({ phase: "created", watch: result.data });
    } else {
      setState({ phase: "editing", error: result.message });
    }
  }

  function startAnother() {
    setValues(emptyForm());
    setAutoBook(false);
    setState({ phase: "editing", error: null });
  }

  if (state.phase === "created") {
    return (
      <WatchCreatedCard
        watch={state.watch}
        onAnother={allowAnother ? startAnother : undefined}
      />
    );
  }

  const band =
    values.timeMode === "exact"
      ? formatTimeBand(values.time || null, null)
      : formatTimeBand(null, { start: values.windowStart, end: values.windowEnd });
  const criteria = [
    known?.name ?? (values.venueName.trim() || "the venue"),
    `party of ${values.partySize || "?"}`,
    formatDate(values.date || null) ?? "the date you pick",
    band ?? "the time you pick",
  ].join(" · ");

  return (
    <form
      className="watch-form"
      onSubmit={onSubmit}
      data-busy={busy}
      noValidate
      aria-label="Set up a watch"
    >
      <div className="form-grid">
        <div className="form-cell wide">
          <label className="form-label" htmlFor={`${id}-venue`}>
            VENUE
          </label>
          <input
            id={`${id}-venue`}
            className="form-input"
            type="text"
            autoComplete="off"
            list={`${id}-venues`}
            placeholder="The Bauer Kitchen"
            maxLength={200}
            value={values.venueName}
            disabled={busy}
            onChange={(e) => onVenueChange(e.target.value)}
          />
          <datalist id={`${id}-venues`}>
            {venues.map((venue) => (
              <option key={venue.name} value={venue.name} />
            ))}
          </datalist>
          <span className="form-hint">
            {known
              ? `Known venue — ${known.venue_type === "RECREATION" ? "activity" : "restaurant"} hours apply.`
              : "Pick a suggestion, or type any Kitchener–Waterloo venue."}
          </span>
        </div>

        {!known && (
          <div className="form-cell wide">
            <label className="form-label" htmlFor={`${id}-type`}>
              KIND OF PLACE
            </label>
            <select
              id={`${id}-type`}
              className="form-input"
              value={values.venueType}
              disabled={busy}
              onChange={(e) => update("venueType", e.target.value as VenueType)}
            >
              {VENUE_TYPES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
        )}

        <div className="form-cell">
          <label className="form-label" htmlFor={`${id}-party`}>
            PARTY SIZE
          </label>
          <input
            id={`${id}-party`}
            className="form-input"
            type="number"
            inputMode="numeric"
            min={1}
            max={100}
            step={1}
            value={values.partySize}
            disabled={busy}
            onChange={(e) => update("partySize", e.target.value)}
          />
        </div>

        <div className="form-cell">
          <label className="form-label" htmlFor={`${id}-date`}>
            DATE
          </label>
          <input
            id={`${id}-date`}
            className="form-input"
            type="date"
            min={today}
            value={values.date}
            disabled={busy}
            onChange={(e) => update("date", e.target.value)}
          />
        </div>

        <fieldset className="form-cell wide form-fieldset">
          <legend className="form-label">TIME</legend>
          <div className="segmented" role="radiogroup" aria-label="Time">
            <label className="segment">
              <input
                type="radio"
                name={`${id}-time-mode`}
                checked={values.timeMode === "exact"}
                disabled={busy}
                onChange={() => update("timeMode", "exact")}
              />
              <span>Exact time</span>
            </label>
            <label className="segment">
              <input
                type="radio"
                name={`${id}-time-mode`}
                checked={values.timeMode === "range"}
                disabled={busy}
                onChange={() => update("timeMode", "range")}
              />
              <span>Time range</span>
            </label>
          </div>
          {values.timeMode === "exact" ? (
            <div className="time-row">
              <label className="sr-only" htmlFor={`${id}-time`}>
                Time
              </label>
              <input
                id={`${id}-time`}
                className="form-input"
                type="time"
                step={900}
                value={values.time}
                disabled={busy}
                onChange={(e) => update("time", e.target.value)}
              />
            </div>
          ) : (
            <div className="time-row">
              <label className="sr-only" htmlFor={`${id}-from`}>
                From
              </label>
              <input
                id={`${id}-from`}
                className="form-input"
                type="time"
                step={900}
                value={values.windowStart}
                disabled={busy}
                onChange={(e) => update("windowStart", e.target.value)}
              />
              <span className="time-to" aria-hidden="true">
                to
              </span>
              <label className="sr-only" htmlFor={`${id}-to`}>
                To
              </label>
              <input
                id={`${id}-to`}
                className="form-input"
                type="time"
                step={900}
                value={values.windowEnd}
                disabled={busy}
                onChange={(e) => update("windowEnd", e.target.value)}
              />
            </div>
          )}
        </fieldset>

        <fieldset className="form-cell wide form-fieldset">
          <legend className="form-label">WHEN A TABLE OPENS</legend>
          <div className="choices">
            <label className="choice" data-selected={!autoBook}>
              <input
                type="radio"
                name={`${id}-mode`}
                checked={!autoBook}
                disabled={busy}
                onChange={() => setAutoBook(false)}
              />
              <span className="choice-title">Notify me</span>
              <span className="choice-text">
                It shows on your watches page, and you get an email if
                you&rsquo;ve added one. You book it yourself.
              </span>
            </label>
            <label className="choice" data-selected={autoBook}>
              <input
                type="radio"
                name={`${id}-mode`}
                checked={autoBook}
                disabled={busy}
                onChange={() => setAutoBook(true)}
              />
              <span className="choice-title">Auto-book</span>
              <span className="choice-text">
                Dibs books the first matching table for you.
              </span>
            </label>
          </div>
        </fieldset>
      </div>

      {autoBook && (
        <div className="criteria" role="note">
          <div className="criteria-head">DIBS WILL BOOK THE FIRST TABLE MATCHING</div>
          <div className="criteria-body">{criteria}</div>
          <div className="criteria-note">
            Demo mode: bookings are simulated. No real table is reserved.
          </div>
        </div>
      )}

      {state.phase === "editing" && state.error && (
        <div className="form-error" role="alert">
          {state.error}
        </div>
      )}

      <button type="submit" className="acquire form-submit" disabled={busy}>
        {busy
          ? "STARTING…"
          : autoBook
            ? "WATCH & AUTO-BOOK ▸"
            : (submitLabel ?? "START WATCH ▸")}
      </button>
    </form>
  );
}

function WatchCreatedCard({
  watch,
  onAnother,
}: {
  watch: Watch;
  onAnother?: () => void;
}) {
  const { query } = watch;
  const meta = [
    `PARTY ${query.party_size}`,
    formatDate(query.date),
    formatTimeBand(query.preferred_time, query.time_window),
  ].filter(Boolean) as string[];

  return (
    <div className="result" role="status" aria-live="assertive">
      <div className="result-rail" data-tone="amber" />
      <div className="result-body">
        <div className="result-head">
          <div>
            <div className="kicker" data-tone="amber">
              WATCH CREATED · {watch.auto_book ? "AUTO-BOOK ON" : "NOTIFY ME"}
            </div>
            <div className="result-title">{query.venue_name}</div>
            <div className="meta">
              {meta.map((item, i) => (
                <span key={item} style={{ display: "contents" }}>
                  {i > 0 && (
                    <span className="sep" aria-hidden="true">
                      ·
                    </span>
                  )}
                  <span>{item}</span>
                </span>
              ))}
            </div>
          </div>
          <span className="pill" data-tone="amber">
            <span className="dot live" data-tone="amber" aria-hidden="true" />
            LOCKED
          </span>
        </div>
        <div className="divider" />
        <div className="result-foot">
          <div className="result-text" style={{ marginTop: 0 }}>
            {watch.auto_book
              ? "Dibs checks every few minutes and books the first matching table — simulated in this demo."
              : "Dibs checks every few minutes and flags the first matching table on your watches page."}
          </div>
          <div className="result-actions">
            {onAnother && (
              <button type="button" className="result-link" onClick={onAnother}>
                SET UP ANOTHER
              </button>
            )}
            <a className="result-link" href="/watches">
              TRACK IT ▸
            </a>
          </div>
        </div>
        <GuestAlerts />
      </div>
    </div>
  );
}
