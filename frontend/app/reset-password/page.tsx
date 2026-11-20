/**
 * Where a password reset email lands. A server-rendered shell around the one
 * interactive island, `ResetPassword`, like the account page.
 */

import { Masthead } from "../_components/Masthead";
import { ResetPassword } from "../_components/ResetPassword";
import { ScopeBackdrop } from "../_components/Scope";

export const metadata = {
  title: "Reset Password · Dibs",
};

export default function ResetPasswordPage() {
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
            Choose a <em>new password.</em>
          </h1>
        </section>

        <ResetPassword />
      </div>
    </main>
  );
}
