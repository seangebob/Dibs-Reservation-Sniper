/**
 * The single boundary between the frontend and the Dibs backend.
 *
 * Every call goes through `apiFetch`, which attaches the anonymous client id
 * (Requirement 2.2) and the session bearer token when one exists (Milestone 5,
 * Requirement 5.2), and normalizes ALL failures — network, non-2xx, malformed
 * body — into a discriminated `ApiResult`, so no page component ever handles a
 * raw fetch rejection or renders a raw error object (Requirement 1.7).
 */

import { hasSession, markSignedIn, markSignedOut } from "./auth";
import { getClientId } from "./client-id";
import type {
  AuthResponse,
  AvailabilityQuery,
  PromptExecutionResult,
  User,
  VenueSummary,
  Watch,
} from "@/types/api";

export type ApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; message: string; status?: number };

const DEFAULT_BASE_URL = "http://localhost:8000";

/**
 * The backend base URL. Reads `NEXT_PUBLIC_API_BASE_URL` (inlined at build
 * time). Without it, `next dev` forwards same-origin `/api/*` to the backend
 * (see next.config.mjs), so the base is empty and local development needs no
 * CORS setup; any other build falls back to localhost.
 */
export function apiBaseUrl(): string {
  const configured = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
  if (configured && configured.length > 0) return configured;
  return process.env.NODE_ENV === "development" ? "" : DEFAULT_BASE_URL;
}

async function errorMessage(response: Response): Promise<string> {
  // The backend serializes every handled error as `{ "detail": "..." }`;
  // request validation (422) sends `{ "detail": [{ "msg": "..." }, ...] }`.
  try {
    const body: unknown = await response.json();
    const detail =
      body && typeof body === "object" && "detail" in body
        ? (body as { detail: unknown }).detail
        : undefined;
    if (typeof detail === "string") {
      return detail;
    }
    const first: unknown = Array.isArray(detail) ? detail[0] : undefined;
    if (
      first &&
      typeof first === "object" &&
      typeof (first as { msg?: unknown }).msg === "string"
    ) {
      // Pydantic prefixes custom validator messages with "Value error, ".
      const msg = (first as { msg: string }).msg.replace(/^Value error, /, "");
      return msg.charAt(0).toUpperCase() + msg.slice(1);
    }
  } catch {
    // fall through to a status-based default
  }
  if (response.status >= 500) {
    return "The server ran into a problem. Please try again in a moment.";
  }
  if (response.status === 404) {
    return "That could not be found.";
  }
  return "Something went wrong with that request.";
}

/**
 * `omitSession` marks calls that start a session (signup, login, reset): a 401
 * there is about the submitted credentials, so it must not sign the browser
 * out. They still send `X-Dibs-Client-Id`, so the backend can claim that
 * visitor's anonymous watches.
 */
type FetchOptions = { omitSession?: boolean };

/**
 * Every request carries this header. The session is a cookie the browser
 * attaches by itself, so the server refuses cookie-authenticated writes
 * without it: another site can't add it to a forged request.
 */
const CSRF_HEADER = "X-Dibs-CSRF";

async function apiFetch<T>(
  path: string,
  init?: RequestInit,
  options?: FetchOptions,
): Promise<ApiResult<T>> {
  const headers = new Headers(init?.headers);
  if (!headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const clientId = getClientId();
  if (clientId) {
    headers.set("X-Dibs-Client-Id", clientId);
  }
  headers.set(CSRF_HEADER, "1");

  let response: Response;
  try {
    // "include" so the session cookie also goes to an API on another origin
    // (Docker, a deployment); same-origin requests send it anyway.
    response = await fetch(`${apiBaseUrl()}${path}`, {
      ...init,
      headers,
      credentials: "include",
    });
  } catch {
    return {
      ok: false,
      message: "Couldn't reach the server. Check your connection and try again.",
    };
  }

  if (!response.ok) {
    // The session is gone (expired or revoked): drop to logged-out rather
    // than asking again with a cookie that can never work.
    if (response.status === 401 && !options?.omitSession && hasSession()) {
      markSignedOut();
    }
    return { ok: false, status: response.status, message: await errorMessage(response) };
  }

  if (response.status === 204) {
    return { ok: true, data: undefined as T };
  }
  try {
    // An empty body (204, or a 202 "accepted") carries no data.
    const text = await response.text();
    if (text.trim() === "") {
      return { ok: true, data: undefined as T };
    }
    return { ok: true, data: JSON.parse(text) as T };
  } catch {
    return {
      ok: false,
      status: response.status,
      message: "The server sent an unexpected response.",
    };
  }
}

/** POST a raw prompt for parsing, booking, or watch creation. */
export function parseAndBook(
  prompt: string,
): Promise<ApiResult<PromptExecutionResult>> {
  return apiFetch("/api/parse-and-book", {
    method: "POST",
    body: JSON.stringify({ prompt }),
  });
}

/** GET the calling client's own watches (owner-scoped, durable). */
export function listMyWatches(): Promise<ApiResult<Watch[]>> {
  return apiFetch("/api/watches/mine", { method: "GET" });
}

/** DELETE (cancel) a watch by id; returns its resolved final state. */
export function cancelWatch(watchId: string): Promise<ApiResult<Watch>> {
  return apiFetch(`/api/watches/${encodeURIComponent(watchId)}`, {
    method: "DELETE",
  });
}

/** GET the venue catalog, for the watch form's suggestions. */
export function listVenues(): Promise<ApiResult<VenueSummary[]>> {
  return apiFetch("/api/venues", { method: "GET" });
}

/** POST a fully-validated query to open a watch directly. */
export function createWatch(
  query: AvailabilityQuery,
  autoBook = false,
): Promise<ApiResult<Watch>> {
  return apiFetch(`/api/watches?auto_book=${autoBook ? "true" : "false"}`, {
    method: "POST",
    body: JSON.stringify(query),
  });
}

// -- email alerts for guests ------------------------------------------------

/** The address this browser's alerts go to, once confirmed. */
export interface AlertEmail {
  email: string | null;
}

/** GET this browser's confirmed alert address (null when there is none). */
export function getAlertEmail(): Promise<ApiResult<AlertEmail>> {
  return apiFetch("/api/alerts/email", { method: "GET" });
}

/** POST an address; the server emails a single-use confirmation link (202). */
export function requestAlertEmail(email: string): Promise<ApiResult<void>> {
  return apiFetch("/api/alerts/email", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

/** POST a confirmation link's token; its address starts getting alerts. */
export function verifyAlertEmail(token: string): Promise<ApiResult<AlertEmail>> {
  return apiFetch("/api/alerts/email/verify", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}

/** DELETE this browser's alert address: no more alert emails. */
export function removeAlertEmail(): Promise<ApiResult<void>> {
  return apiFetch("/api/alerts/email", { method: "DELETE" });
}

// -- accounts ---------------------------------------------------------------

async function establishSession(
  path: string,
  email: string,
  password: string,
): Promise<ApiResult<AuthResponse>> {
  const result = await apiFetch<AuthResponse>(
    path,
    { method: "POST", body: JSON.stringify({ email, password }) },
    // A wrong password's 401 must not sign out an existing session.
    { omitSession: true },
  );
  if (result.ok) {
    markSignedIn();
  }
  return result;
}

/** Create an account and start a session. */
export function signup(
  email: string,
  password: string,
): Promise<ApiResult<AuthResponse>> {
  return establishSession("/api/auth/signup", email, password);
}

/** Start a session for an existing account. */
export function login(
  email: string,
  password: string,
): Promise<ApiResult<AuthResponse>> {
  return establishSession("/api/auth/login", email, password);
}

/**
 * End the session: the server revokes it and clears the cookie. The local
 * hint is cleared whatever the server says, so a network failure or an
 * already-dead session still shows the user as logged out here.
 */
export async function logout(): Promise<ApiResult<void>> {
  const result = await apiFetch<void>("/api/auth/logout", { method: "POST" });
  markSignedOut();
  return result;
}

/**
 * Ask for a password reset link. The answer is the same whether or not the
 * account exists, so this can't be used to discover accounts.
 */
export function forgotPassword(email: string): Promise<ApiResult<void>> {
  return apiFetch<void>(
    "/api/auth/password/forgot",
    { method: "POST", body: JSON.stringify({ email }) },
    { omitSession: true },
  );
}

/** Use a reset link's token to set a new password; signs this browser in. */
export async function resetPassword(
  token: string,
  password: string,
): Promise<ApiResult<AuthResponse>> {
  const result = await apiFetch<AuthResponse>(
    "/api/auth/password/reset",
    { method: "POST", body: JSON.stringify({ token, password }) },
    { omitSession: true },
  );
  if (result.ok) {
    markSignedIn();
  }
  return result;
}

/**
 * Change the signed-in account's password. The server signs out every
 * session and sets a fresh session cookie for this browser.
 */
export function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<ApiResult<AuthResponse>> {
  return apiFetch<AuthResponse>("/api/auth/password/change", {
    method: "POST",
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
}

/** Use an account confirmation link's token; returns the confirmed account. */
export function confirmEmail(token: string): Promise<ApiResult<User>> {
  return apiFetch<User>(
    "/api/auth/email/confirm",
    { method: "POST", body: JSON.stringify({ token }) },
    { omitSession: true },
  );
}

/** Email the signed-in account another confirmation link (202). */
export function resendConfirmation(): Promise<ApiResult<void>> {
  return apiFetch<void>("/api/auth/email/resend", { method: "POST" });
}

/** GET the signed-in account, used to hydrate on load. 401 → logged out. */
export function me(): Promise<ApiResult<User>> {
  return apiFetch<User>("/api/auth/me", { method: "GET" });
}
