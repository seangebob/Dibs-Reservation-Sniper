/**
 * The session token store (Milestone 5, Requirement 5.2).
 *
 * Holds the opaque bearer token the backend issues at signup/login. Unlike the
 * anonymous client id in `client-id.ts`, this is never generated here — it only
 * ever comes from the server, and clearing it is exactly "log out".
 */

const STORAGE_KEY = "dibs.session-token";

// Fallback for browsers where `localStorage` throws (private mode): the session
// then lives for the tab only. A token written here but not to storage can go
// stale if another tab logs out; that self-heals on the next 401, which clears
// the token (see `lib/api.ts`).
let memoryToken: string | null = null;

/** The current session token, or null when logged out or server-rendering. */
export function getSessionToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(STORAGE_KEY) ?? memoryToken;
  } catch {
    return memoryToken;
  }
}

/** Persist the token issued by signup/login. */
export function setSessionToken(token: string): void {
  memoryToken = token;
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, token);
  } catch {
    // Storage disabled or full: the in-memory token still covers this tab.
  }
}

/** Forget the session — on logout, or when the server rejects the token. */
export function clearSessionToken(): void {
  memoryToken = null;
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Nothing to remove if storage is unavailable.
  }
}

/** Test-only: drop the in-memory fallback so each test starts clean. */
export function __resetSessionTokenForTests(): void {
  memoryToken = null;
}
