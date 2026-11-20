/**
 * Where an account confirmation email lands. A server-rendered shell around
 * the one interactive island, `ConfirmEmail`.
 */

import { ConfirmEmail } from "../_components/ConfirmEmail";
import { Masthead } from "../_components/Masthead";
import { ScopeBackdrop } from "../_components/Scope";

export const metadata = {
  title: "Confirm Email · Dibs",
};

export default function ConfirmEmailPage() {
  return (
    <main className="shell">
      <ScopeBackdrop />

      <div className="layer">
        <Masthead current="account" />

        <section className="hero compact">
          <div className="eyebrow">
            <span className="dot" data-tone="amber" aria-hidden="true" />
            IDENTIFY
          </div>
          <h1>
            Confirm <em>your email.</em>
          </h1>
        </section>

        <ConfirmEmail />
      </div>
    </main>
  );
}
