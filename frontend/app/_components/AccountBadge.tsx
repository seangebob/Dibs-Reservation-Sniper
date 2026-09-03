"use client";

/**
 * The account corner of the masthead (Requirement 5.1): the signed-in email and
 * a log-out action, or a way in when logged out. The one client island in an
 * otherwise server-rendered header, so only this much JS ships for it.
 *
 * Logging out navigates to the front door rather than mutating state in place:
 * the app links between pages with plain anchors, so a full load is what keeps
 * every other view (the dashboard especially) consistent with the new identity.
 */

import { useState } from "react";
import { useAuth } from "./AuthProvider";

export function AccountBadge() {
  const { user, loading, logout } = useAuth();
  const [busy, setBusy] = useState(false);

  // Render nothing until hydration settles, so the header never flashes
  // "SIGN IN" at someone who is already signed in.
  if (loading) return null;

  if (user === null) {
    return (
      <a className="account-link" href="/account">
        SIGN IN
      </a>
    );
  }

  async function onLogout() {
    setBusy(true);
    await logout();
    window.location.assign("/");
  }

  return (
    <span className="account">
      <span className="account-email" title={user.email}>
        {user.email}
      </span>
      <button
        type="button"
        className="account-link"
        disabled={busy}
        onClick={() => void onLogout()}
      >
        {busy ? "SIGNING OUT…" : "LOG OUT"}
      </button>
    </span>
  );
}
