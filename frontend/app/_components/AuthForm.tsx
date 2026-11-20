"use client";

/**
 * The sign-in / sign-up form (Requirement 5.1).
 *
 * One form, two modes — the fields are identical, so a toggle beats a second
 * component. Failures render the backend's plain-language `detail` and nothing
 * else: no stack trace, no raw JSON, and never the password (Requirement 5.5).
 */

import { useState } from "react";
import { changePassword, forgotPassword } from "@/lib/api";
import { useAuth } from "./AuthProvider";

type Mode = "login" | "signup";

const COPY: Record<Mode, { action: string; busy: string; alt: string; altCta: string }> = {
  login: {
    action: "SIGN IN ▸",
    busy: "SIGNING IN…",
    alt: "No account yet?",
    altCta: "Create one",
  },
  signup: {
    action: "CREATE ACCOUNT ▸",
    busy: "CREATING…",
    alt: "Already have an account?",
    altCta: "Sign in",
  },
};

export function AuthForm() {
  const { user, login, signup } = useAuth();
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [forgotten, setForgotten] = useState(false);

  const copy = COPY[mode];
  const ready = email.trim().length > 0 && password.length > 0;

  if (user !== null) {
    return (
      <div className="auth-panel" role="status">
        <div className="kicker" data-tone="green">
          SIGNED IN
        </div>
        <div className="result-title sm" style={{ marginTop: 8 }}>
          {user.email}
        </div>
        <p className="auth-note">
          Your watches now follow this account on any device you sign in from.
        </p>
        <a className="empty-cta" href="/watches">
          SEE YOUR WATCHES ▸
        </a>
        <ChangePassword />
      </div>
    );
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!ready || busy) return;

    setBusy(true);
    setError(null);
    const result = await (mode === "login" ? login : signup)(email, password);
    setBusy(false);

    if (result.ok) {
      // A full load, matching how the rest of the app navigates, so the
      // dashboard mounts fresh against the new identity (Requirement 5.4).
      window.location.assign("/watches");
      return;
    }
    setError(result.message);
    setPassword("");
  }

  function switchMode() {
    setMode((m) => (m === "login" ? "signup" : "login"));
    setError(null);
  }

  if (forgotten) {
    return (
      <ForgotPassword
        initialEmail={email}
        onBack={() => {
          setForgotten(false);
          setError(null);
        }}
      />
    );
  }

  return (
    <div className="auth-panel">
      <form className="auth-form" onSubmit={onSubmit} data-busy={busy}>
        <label className="auth-label" htmlFor="email">
          EMAIL
        </label>
        <input
          id="email"
          type="email"
          autoComplete="email"
          className="auth-input"
          value={email}
          disabled={busy}
          onChange={(e) => setEmail(e.target.value)}
        />

        <label className="auth-label" htmlFor="password">
          PASSWORD
        </label>
        <input
          id="password"
          type="password"
          autoComplete={mode === "login" ? "current-password" : "new-password"}
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

        <button type="submit" className="acquire" disabled={busy || !ready}>
          {busy ? copy.busy : copy.action}
        </button>
      </form>

      {mode === "login" && (
        <p className="auth-alt">
          <button type="button" className="result-link" onClick={() => setForgotten(true)}>
            Forgot password?
          </button>
        </p>
      )}

      <p className="auth-alt">
        {copy.alt}{" "}
        <button type="button" className="result-link" onClick={switchMode}>
          {copy.altCta}
        </button>
      </p>

      <p className="auth-note">
        Signing in claims the watches you opened on this device, so they stay
        yours on any other.
      </p>
    </div>
  );
}

/**
 * "Forgot password?": email a reset link. The confirmation reads the same
 * whether or not the account exists, as the server's answer does.
 */
function ForgotPassword({
  initialEmail,
  onBack,
}: {
  initialEmail: string;
  onBack: () => void;
}) {
  const [email, setEmail] = useState(initialEmail);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sentTo, setSentTo] = useState<string | null>(null);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    const address = email.trim();
    if (address === "" || busy) return;
    setBusy(true);
    setError(null);
    const result = await forgotPassword(address);
    setBusy(false);
    if (result.ok) {
      setSentTo(address);
    } else {
      setError(result.message);
    }
  }

  if (sentTo !== null) {
    return (
      <div className="auth-panel" role="status">
        <div className="kicker" data-tone="green">
          CHECK YOUR INBOX
        </div>
        <p className="auth-note">
          If there&rsquo;s an account for <strong>{sentTo}</strong>, we&rsquo;ve
          emailed it a link to choose a new password. The link works once and
          expires in 1 hour.
        </p>
        <p className="auth-alt">
          <button type="button" className="result-link" onClick={onBack}>
            Back to sign in
          </button>
        </p>
      </div>
    );
  }

  return (
    <div className="auth-panel">
      <form className="auth-form" onSubmit={onSubmit} data-busy={busy}>
        <label className="auth-label" htmlFor="forgot-email">
          EMAIL
        </label>
        <input
          id="forgot-email"
          type="email"
          autoComplete="email"
          className="auth-input"
          value={email}
          disabled={busy}
          onChange={(e) => setEmail(e.target.value)}
        />
        {error && (
          <div className="auth-error" role="alert">
            {error}
          </div>
        )}
        <button type="submit" className="acquire" disabled={busy || email.trim() === ""}>
          {busy ? "SENDING…" : "SEND RESET LINK ▸"}
        </button>
      </form>
      <p className="auth-alt">
        Remembered it?{" "}
        <button type="button" className="result-link" onClick={onBack}>
          Sign in
        </button>
      </p>
    </div>
  );
}

/**
 * Change the password while signed in. Needs the current one; on success every
 * other device is signed out and this browser keeps going on a fresh session.
 */
function ChangePassword() {
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  if (!open) {
    return (
      <p className="auth-alt">
        <button type="button" className="result-link" onClick={() => setOpen(true)}>
          Change password
        </button>
        {done && (
          <span className="auth-note" role="status">
            {" "}
            Password changed. Other devices are signed out.
          </span>
        )}
      </p>
    );
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (busy || current.length === 0 || next.length === 0) return;
    setBusy(true);
    setError(null);
    const result = await changePassword(current, next);
    setBusy(false);
    if (result.ok) {
      setCurrent("");
      setNext("");
      setDone(true);
      setOpen(false);
      return;
    }
    setError(result.message);
  }

  return (
    <form className="auth-form change-password" onSubmit={onSubmit} data-busy={busy}>
      <label className="auth-label" htmlFor="current-password">
        CURRENT PASSWORD
      </label>
      <input
        id="current-password"
        type="password"
        autoComplete="current-password"
        className="auth-input"
        value={current}
        disabled={busy}
        onChange={(e) => setCurrent(e.target.value)}
      />
      <label className="auth-label" htmlFor="changed-password">
        NEW PASSWORD
      </label>
      <input
        id="changed-password"
        type="password"
        autoComplete="new-password"
        className="auth-input"
        value={next}
        disabled={busy}
        onChange={(e) => setNext(e.target.value)}
      />
      {error && (
        <div className="auth-error" role="alert">
          {error}
        </div>
      )}
      <button
        type="submit"
        className="acquire"
        disabled={busy || current.length === 0 || next.length === 0}
      >
        {busy ? "SAVING…" : "CHANGE PASSWORD ▸"}
      </button>
      <p className="auth-note">This signs out every other device.</p>
    </form>
  );
}
