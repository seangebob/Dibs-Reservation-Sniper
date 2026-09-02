/**
 * The prompt page — Dibs' front door.
 *
 * A server-rendered Night Scope shell (masthead, hero, scope furniture) wraps
 * the one interactive island, `PromptConsole`, which owns the input and the
 * response state machine. Keeping the shell on the server means only the
 * console's logic ships to the browser.
 */

import { Masthead } from "./_components/Masthead";
import { PromptConsole } from "./_components/PromptConsole";
import { ScopeBackdrop } from "./_components/Scope";

export default function Home() {
  return (
    <main className="shell">
      <ScopeBackdrop />

      <div className="layer">
        <Masthead current="new" />

        <section className="hero">
          <div className="eyebrow">
            <span
              className="dot live"
              data-tone="amber"
              aria-hidden="true"
            />
            TRACKING · KITCHENER–WATERLOO
          </div>
          <h1>
            Lock the table
            <br />
            they keep <em>losing.</em>
          </h1>
          <p>
            Describe the reservation in plain words. We keep it in the
            crosshairs around the clock and take the shot the instant a table
            frees up.
          </p>
        </section>

        <PromptConsole />
      </div>
    </main>
  );
}
