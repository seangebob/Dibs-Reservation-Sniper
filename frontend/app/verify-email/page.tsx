/**
 * Where a guest's confirmation email lands. A server-rendered shell around the
 * one interactive island, `VerifyEmail`, like the watches page.
 */

import { Masthead } from "../_components/Masthead";
import { ScopeBackdrop } from "../_components/Scope";
import { VerifyEmail } from "../_components/VerifyEmail";

export const metadata = {
  title: "Confirm Email · Dibs",
};

export default function VerifyEmailPage() {
  return (
    <main className="shell">
      <ScopeBackdrop />

      <div className="layer">
        <Masthead />

        <section className="hero compact">
          <div className="eyebrow">
            <span className="dot" data-tone="amber" aria-hidden="true" />
            EMAIL ALERTS
          </div>
          <h1>
            Confirm <em>your email.</em>
          </h1>
        </section>

        <VerifyEmail />
      </div>
    </main>
  );
}
