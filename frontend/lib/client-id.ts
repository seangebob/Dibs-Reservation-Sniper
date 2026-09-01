/**
 * The anonymous client identity (Milestone 4, Requirement 2).
 *
 * There is no login: the site remembers "which watches are mine" via an opaque
 * token the browser generates once and persists in `localStorage`, sent to the
 * backend as the `X-Dibs-Client-Id` header. It is not authentication and grants
 * no access — it only scopes the "my watches" listing.
 */

const STORAGE_KEY = "dibs.client-id";

/** Characters the backend accepts for `X-Dibs-Client-Id` (`^[A-Za-z0-9_-]{1,200}$`). */
const FALLBACK_ALPHABET =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";

// A per-tab cache so the id stays stable within a session even when
// localStorage is unavailable (private mode), where each read would otherwise
// have to regenerate it.
let cached: string | null = null;

function generateId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    // A UUID's hex + hyphens already satisfy the backend's accepted charset.
    return crypto.randomUUID();
  }
  let out = "";
  for (let i = 0; i < 32; i += 1) {
    out += FALLBACK_ALPHABET[Math.floor(Math.random() * FALLBACK_ALPHABET.length)];
  }
  return out;
}

/**
 * The current visitor's client id, generating and persisting one on first use.
 *
 * Returns `null` during server-side rendering, where there is no browser
 * storage — callers attach the header only when a non-null id is available, so
 * a request made before hydration simply goes out unowned (which the backend
 * accepts, Requirement 2.4).
 */
export function getClientId(): string | null {
  if (typeof window === "undefined") return null;
  if (cached !== null) return cached;

  try {
    const existing = window.localStorage.getItem(STORAGE_KEY);
    if (existing) {
      cached = existing;
      return cached;
    }
    const fresh = generateId();
    window.localStorage.setItem(STORAGE_KEY, fresh);
    cached = fresh;
    return cached;
  } catch {
    // Storage disabled or full (e.g. private browsing): still return a stable
    // per-tab id so the header is sent, it just will not survive a reload.
    cached = generateId();
    return cached;
  }
}

/** Test-only: drop the in-memory cache so each test starts clean. */
export function __resetClientIdCacheForTests(): void {
  cached = null;
}
