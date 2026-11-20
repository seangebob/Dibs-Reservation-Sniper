import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  __resetSessionForTests,
  hasSession,
  markSignedIn,
} from "./auth";
import { __resetClientIdCacheForTests } from "./client-id";
import {
  apiBaseUrl,
  cancelWatch,
  listMyWatches,
  listVenues,
  login,
  logout,
  me,
  parseAndBook,
  signup,
} from "./api";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("apiBaseUrl", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("falls back to localhost when unset outside the dev server", () => {
    // NEXT_PUBLIC_API_BASE_URL is not set in the test environment.
    expect(apiBaseUrl()).toBe("http://localhost:8000");
  });

  it("is same-origin under the dev server, which forwards /api to the backend", () => {
    vi.stubEnv("NODE_ENV", "development");

    expect(apiBaseUrl()).toBe("");
  });

  it("uses NEXT_PUBLIC_API_BASE_URL whenever it is set", () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", " https://api.example.com ");

    expect(apiBaseUrl()).toBe("https://api.example.com");
  });
});

describe("apiFetch (via typed wrappers)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    __resetClientIdCacheForTests();
    __resetSessionForTests();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns ok:true with the parsed body on success", async () => {
    const payload = { status: "WATCH_CREATED", watch_id: "watch_1", message: "ok" };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(payload));

    const result = await parseAndBook("watch Cote for 4");

    expect(result).toEqual({ ok: true, data: payload });
  });

  it("attaches the client id and content-type headers", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ status: "NO_AVAILABILITY", message: "" }));

    await parseAndBook("anything");

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    const headers = new Headers(init.headers);
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.get("X-Dibs-Client-Id")).toMatch(/^[A-Za-z0-9_-]{1,200}$/);
  });

  it("targets the configured base URL and path", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse([]));

    await listMyWatches();

    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://localhost:8000/api/watches/mine",
    );
  });

  it("normalizes a non-2xx body's detail into ok:false", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ detail: "Cannot watch a reservation date in the past" }, 422),
    );

    const result = await parseAndBook("watch yesterday");

    expect(result).toEqual({
      ok: false,
      status: 422,
      message: "Cannot watch a reservation date in the past",
    });
  });

  it("uses a friendly default when a 5xx has no detail", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("boom", { status: 500 }));

    const result = await parseAndBook("anything");

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.status).toBe(500);
      expect(result.message).toMatch(/server ran into a problem/i);
    }
  });

  it("normalizes a network failure into ok:false without throwing", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));

    const result = await listMyWatches();

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.message).toMatch(/couldn't reach the server/i);
      expect(result.status).toBeUndefined();
    }
  });

  it("url-encodes the watch id when cancelling", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ watch_id: "a/b", status: "CANCELLED" }));

    await cancelWatch("a/b");

    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://localhost:8000/api/watches/a%2Fb",
    );
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("DELETE");
  });
});

function headersOf(fetchMock: { mock: { calls: unknown[][] } }, call = 0): Headers {
  return new Headers((fetchMock.mock.calls[call][1] as RequestInit).headers);
}

const AUTH_BODY = {
  token: "tok-new",
  user: { id: "u1", email: "a@x.com", created_at: "2026-09-03T12:00:00Z" },
};

describe("session handling", () => {
  beforeEach(() => {
    window.localStorage.clear();
    __resetClientIdCacheForTests();
    __resetSessionForTests();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("sends the session cookie and the CSRF header, never a bearer token", async () => {
    markSignedIn();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse([]));

    await listMyWatches();

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.credentials).toBe("include");
    const headers = headersOf(fetchMock);
    expect(headers.get("X-Dibs-CSRF")).toBe("1");
    expect(headers.has("Authorization")).toBe(false);
    expect(headers.get("X-Dibs-Client-Id")).toMatch(/^[A-Za-z0-9_-]{1,200}$/);
  });

  it("notes the sign-in after signup, without storing the token", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(AUTH_BODY, 201));

    const result = await signup("a@x.com", "hunter2-secret");

    expect(result.ok).toBe(true);
    expect(hasSession()).toBe(true);
    expect(JSON.stringify(window.localStorage)).not.toContain("tok-new");
  });

  it("notes the sign-in after login", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(AUTH_BODY));

    await login("a@x.com", "hunter2-secret");

    expect(hasSession()).toBe(true);
  });

  it("still sends the client id on login so anonymous watches are claimed", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(AUTH_BODY));

    await login("a@x.com", "hunter2-secret");

    expect(headersOf(fetchMock).get("X-Dibs-Client-Id")).toMatch(/^[A-Za-z0-9_-]{1,200}$/);
  });

  it("keeps an existing session when a login attempt is rejected", async () => {
    markSignedIn();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ detail: "Invalid email or password." }, 401),
    );

    const result = await login("a@x.com", "wrong");

    expect(result.ok).toBe(false);
    // The 401 is about the submitted credentials, not the session.
    expect(hasSession()).toBe(true);
  });

  it("signs out locally when the server says the session is gone", async () => {
    markSignedIn();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ detail: "Authentication required." }, 401),
    );

    const result = await me();

    expect(result.ok).toBe(false);
    expect(hasSession()).toBe(false);
  });

  it("stays signed in on a non-401 failure", async () => {
    markSignedIn();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({ detail: "nope" }, 500));

    await me();

    expect(hasSession()).toBe(true);
  });

  it("logout signs out locally even when the request fails", async () => {
    markSignedIn();
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));

    const result = await logout();

    expect(result.ok).toBe(false);
    expect(hasSession()).toBe(false);
  });

  it("logout asks the server to revoke the session and clear the cookie", async () => {
    markSignedIn();
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(null, { status: 204 }));

    await logout();

    expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/api\/auth\/logout$/);
    expect((fetchMock.mock.calls[0][1] as RequestInit).credentials).toBe("include");
    expect(hasSession()).toBe(false);
  });
});

describe("watch form calls", () => {
  beforeEach(() => {
    window.localStorage.clear();
    __resetClientIdCacheForTests();
    __resetSessionForTests();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("lists venues with a GET to /api/venues", async () => {
    const venues = [{ name: "The Bauer Kitchen", venue_type: "RESTAURANT", aliases: [] }];
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(venues));

    const result = await listVenues();

    expect(result).toEqual({ ok: true, data: venues });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8000/api/venues");
    expect((init as RequestInit).method).toBe("GET");
  });

  it("turns a request-validation 422 into its first readable message", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(
        {
          detail: [
            {
              type: "value_error",
              loc: ["body"],
              msg: "Value error, availability query requires a time or time window",
            },
          ],
        },
        422,
      ),
    );

    const result = await listVenues();

    expect(result).toEqual({
      ok: false,
      status: 422,
      message: "Availability query requires a time or time window",
    });
  });
});
