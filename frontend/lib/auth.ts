/**
 * Whether this browser is signed in (roadmap 3b).
 *
 * The session itself is an HttpOnly cookie the server sets, which scripts on
 * the page can't read -- that is the point: a malicious script can't steal it.
 * What's kept here is only a "signed in" hint, so the app knows whether to ask
 * `/api/auth/me` on load instead of sending every anonymous visitor's browser
 * a request that can only answer 401. It grants nothing; the cookie does.
 */

const HINT_KEY = "dibs.signed-in";
/** Where the bearer token used to live, before cookie sessions. */
const LEGACY_TOKEN_KEY = "dibs.session-token";

// Fallback for browsers where `localStorage` throws (private mode).
let memoryHint = false;

function storage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/** True when this browser signed in and hasn't signed out since. */
export function hasSession(): boolean {
  const store = storage();
  try {
    // A token from before cookie sessions is a credential at rest; drop it.
    store?.removeItem(LEGACY_TOKEN_KEY);
    return store?.getItem(HINT_KEY) === "1" || memoryHint;
  } catch {
    return memoryHint;
  }
}

/** Remember that the server just signed this browser in. */
export function markSignedIn(): void {
  memoryHint = true;
  try {
    storage()?.setItem(HINT_KEY, "1");
  } catch {
    // Storage disabled or full: the in-memory hint still covers this tab.
  }
}

/** Forget the hint -- on logout, or when the server says the session is gone. */
export function markSignedOut(): void {
  memoryHint = false;
  try {
    storage()?.removeItem(HINT_KEY);
    storage()?.removeItem(LEGACY_TOKEN_KEY);
  } catch {
    // Nothing to remove if storage is unavailable.
  }
}

/** Test-only: drop the in-memory fallback so each test starts clean. */
export function __resetSessionForTests(): void {
  memoryHint = false;
}
