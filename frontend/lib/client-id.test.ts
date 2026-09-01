import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { __resetClientIdCacheForTests, getClientId } from "./client-id";

const STORAGE_KEY = "dibs.client-id";
// Must match the backend's accepted shape: ^[A-Za-z0-9_-]{1,200}$
const ACCEPTED = /^[A-Za-z0-9_-]{1,200}$/;

describe("getClientId", () => {
  beforeEach(() => {
    window.localStorage.clear();
    __resetClientIdCacheForTests();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("generates and persists an id on first use", () => {
    const id = getClientId();

    expect(id).not.toBeNull();
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe(id);
  });

  it("returns a value the backend header pattern accepts", () => {
    expect(getClientId()).toMatch(ACCEPTED);
  });

  it("returns the same id on subsequent calls", () => {
    const first = getClientId();
    __resetClientIdCacheForTests(); // force a re-read from storage, not the cache
    const second = getClientId();

    expect(second).toBe(first);
  });

  it("reuses an id already present in storage", () => {
    window.localStorage.setItem(STORAGE_KEY, "existing-visitor-42");

    expect(getClientId()).toBe("existing-visitor-42");
  });

  it("still returns a valid id when storage throws (private mode)", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("storage disabled");
    });

    const id = getClientId();

    expect(id).toMatch(ACCEPTED);
  });

  it("keeps the same id within a session when storage is unavailable", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("storage disabled");
    });

    expect(getClientId()).toBe(getClientId());
  });
});
