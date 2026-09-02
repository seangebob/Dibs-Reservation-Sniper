/**
 * The watch dashboard — Dibs' second room.
 *
 * A server-rendered Night Scope shell (masthead, heading, scope furniture)
 * wraps the one interactive island, `WatchDashboard`, which loads and manages
 * the calling client's own watches. Keeping the shell on the server means only
 * the dashboard's logic ships to the browser.
 */

import { Masthead } from "../_components/Masthead";
import { ScopeBackdrop } from "../_components/Scope";
import { WatchDashboard } from "../_components/WatchDashboard";

export const metadata = {
  title: "Your Watches · Dibs",
};

export default function WatchesPage() {
  return (
    <main className="shell">
      <ScopeBackdrop />

      <div className="layer">
        <Masthead current="watches" />

        <section className="hero compact">
          <div className="eyebrow">
            <span className="dot live" data-tone="amber" aria-hidden="true" />
            THE BOARD
          </div>
          <h1>
            Everything in <em>the scope.</em>
          </h1>
          <p>
            Every reservation you&rsquo;ve set Dibs on. Cancel any active watch
            and it stands down on the spot.
          </p>
        </section>

        <WatchDashboard />
      </div>
    </main>
  );
}
