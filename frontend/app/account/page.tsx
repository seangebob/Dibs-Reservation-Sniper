/**
 * The account page — sign in or create an account.
 *
 * The same server-rendered Night Scope shell as the other two rooms, wrapping
 * the one interactive island, `AuthForm`.
 */

import { AuthForm } from "../_components/AuthForm";
import { Masthead } from "../_components/Masthead";
import { ScopeBackdrop } from "../_components/Scope";

export const metadata = {
  title: "Account · Dibs",
};

export default function AccountPage() {
  return (
    <main className="shell">
      <ScopeBackdrop />

      <div className="layer">
        <Masthead current="account" />

        <section className="hero compact">
          <div className="eyebrow">
            <span className="dot live" data-tone="amber" aria-hidden="true" />
            IDENTIFY
          </div>
          <h1>
            Keep your watches <em>on you.</em>
          </h1>
          <p>
            An account carries your watches between devices. Without one, Dibs
            still works — this browser just keeps them to itself.
          </p>
        </section>

        <AuthForm />
      </div>
    </main>
  );
}
