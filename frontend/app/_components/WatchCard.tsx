/**
 * One watch, rendered. Pure and presentational — it holds no state and fires
 * no requests; the parent owns the list, the `now` clock (so the "next check"
 * countdown stays fresh), the per-card `cancelling` flag, and the `onCancel`
 * handler. A colour rail plus a status pill make ACTIVE / FOUND / BOOKED /
 * EXPIRED / CANCELLED scannable at a glance (Requirement 4.3).
 */

import type { Watch, WatchStatus } from "@/types/api";
import { formatCountdown, formatDate, formatTimeBand } from "@/lib/format";

type Tone = "amber" | "green" | "muted";

const STATUS_META: Record<
  WatchStatus,
  { label: string; tone: Tone; live: boolean }
> = {
  ACTIVE: { label: "TRACKING", tone: "amber", live: true },
  FOUND: { label: "IN SIGHTS", tone: "green", live: false },
  BOOKED: { label: "CONFIRMED", tone: "green", live: false },
  EXPIRED: { label: "STOOD DOWN", tone: "muted", live: false },
  CANCELLED: { label: "CALLED OFF", tone: "muted", live: false },
};

export function WatchCard({
  watch,
  now,
  cancelling,
  onCancel,
}: {
  watch: Watch;
  now: number;
  cancelling: boolean;
  onCancel: (watchId: string) => void;
}) {
  const meta = STATUS_META[watch.status];
  const isActive = watch.status === "ACTIVE";
  const nextCheck = isActive ? formatCountdown(watch.next_check_at, now) : null;

  const metaItems = [
    `PARTY ${watch.query.party_size}`,
    formatDate(watch.query.date),
    formatTimeBand(watch.query.preferred_time, watch.query.time_window),
  ].filter(Boolean) as string[];

  return (
    <article className="watch">
      <div className="watch-rail" data-tone={meta.tone} />
      <div className="watch-body">
        <div className="watch-head">
          <div>
            <div className="watch-venue">{watch.query.venue_name}</div>
            <div className="meta">
              {metaItems.map((item, i) => (
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
          <span className="pill" data-tone={meta.tone}>
            {meta.live ? (
              <span className="dot live" data-tone="amber" aria-hidden="true" />
            ) : meta.tone === "green" ? (
              <span className="dot" data-tone="green" aria-hidden="true" />
            ) : null}
            {meta.label}
          </span>
        </div>

        <div className="watch-foot">
          <div className="watch-stats">
            <span>
              {watch.attempts} / {watch.max_attempts} checks
            </span>
            {nextCheck && (
              <span className="watch-next">· next {nextCheck}</span>
            )}
            {watch.status === "BOOKED" && watch.booking && (
              <span className="watch-booking">
                · #{watch.booking.booking_id}
              </span>
            )}
          </div>
          {isActive && (
            <button
              type="button"
              className="watch-cancel"
              disabled={cancelling}
              aria-busy={cancelling}
              onClick={() => onCancel(watch.watch_id)}
            >
              {cancelling ? "CANCELLING…" : "CANCEL WATCH"}
            </button>
          )}
        </div>

        {isActive && watch.last_error && (
          <div className="watch-error">
            Last check hit a snag — still tracking.
          </div>
        )}
      </div>
    </article>
  );
}
