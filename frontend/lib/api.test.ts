import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { __resetClientIdCacheForTests } from "./client-id";
import {
  apiBaseUrl,
  cancelWatch,
  listMyWatches,
  parseAndBook,
} from "./api";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("apiBaseUrl", () => {
  it("falls back to localhost when unset", () => {
    // NEXT_PUBLIC_API_BASE_URL is not set in the test environment.
    expect(apiBaseUrl()).toBe("http://localhost:8000");
  });
});

describe("apiFetch (via typed wrappers)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    __resetClientIdCacheForTests();
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
