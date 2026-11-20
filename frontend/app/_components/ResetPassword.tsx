"use client";

/**
 * Where a password reset email lands: choose a new password. On success the
 * browser is signed in and every other session is signed out by the server.
 * The link's code is read once and removed from the address bar, so it
 * doesn't sit in the browser's history.
 */

import { useEffect, useState } from "react";
import { resetPassword } from "@/lib/api";

export function ResetPassword() {
  // undefined: not read yet; null: the link had no code.
  const [token, setToken] = useState<string | null | undefined>(undefined);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const code = new URLSearchParams(window.location.search).get("token")?.trim();
    window.history.replaceState(null, "", window.location.pathname);
    setToken(code ? code : null);
  }, []);

  if (token === undefined) return null;

  if (token === null) {
    return (
      <div className="auth-panel" role="alert">
        <div className="kicker" data-tone="red">
          LINK DIDN&rsquo;T WORK
        </div>
        <p className="auth-note">
          This link is missing its code. Open the link from the email again, or
          ask for a new one.
        </p>
        <a className="empty-cta" href="/account">
          BACK TO SIGN IN ▸
        </a>
      </div>
    );
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!token || password.length === 0 || busy) return;
    setBusy(true);
    setError(null);
    const result = await resetPassword(token, password);
    setBusy(false);
    if (result.ok) {
      window.location.assign("/watches");
      return;
    }
    setError(result.message);
  }

  return (
    <div className="auth-panel">
      <form className="auth-form" onSubmit={onSubmit} data-busy={busy}>
        <label className="auth-label" htmlFor="new-password">
          NEW PASSWORD
        </label>
        <input
          id="new-password"
          type="password"
          autoComplete="new-password"
          className="auth-input"
          value={password}
          disabled={busy}
          onChange={(e) => setPassword(e.target.value)}
        />
        {error && (
          <div className="auth-error" role="alert">
            {error}
          </div>
        )}
        <button type="submit" className="acquire" disabled={busy || password.length === 0}>
          {busy ? "SAVING…" : "SET NEW PASSWORD ▸"}
        </button>
      </form>
      <p className="auth-note">
        This signs you in here and signs out every other device.
      </p>
      {error && (
        <p className="auth-alt">
          <a className="result-link" href="/account">
            Ask for a new link
          </a>
        </p>
      )}
    </div>
  );
}
