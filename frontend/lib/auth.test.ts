import { beforeEach, describe, expect, it } from "vitest";

import {
  __resetSessionTokenForTests,
  clearSessionToken,
  getSessionToken,
  setSessionToken,
} from "./auth";

describe("session token store", () => {
  beforeEach(() => {
    window.localStorage.clear();
    __resetSessionTokenForTests();
  });

  it("is null before anything is stored", () => {
    expect(getSessionToken()).toBeNull();
  });

  it("returns what was set", () => {
    setSessionToken("tok-abc");

    expect(getSessionToken()).toBe("tok-abc");
  });

  it("persists to localStorage so the session survives a reload", () => {
    setSessionToken("tok-abc");

    expect(window.localStorage.getItem("dibs.session-token")).toBe("tok-abc");
    // A "fresh page" keeps only storage, not the in-memory fallback.
    __resetSessionTokenForTests();
    expect(getSessionToken()).toBe("tok-abc");
  });

  it("clearing forgets the token everywhere", () => {
    setSessionToken("tok-abc");

    clearSessionToken();

    expect(getSessionToken()).toBeNull();
    expect(window.localStorage.getItem("dibs.session-token")).toBeNull();
  });

  it("replaces an existing token rather than accumulating", () => {
    setSessionToken("tok-old");
    setSessionToken("tok-new");

    expect(getSessionToken()).toBe("tok-new");
  });
});
