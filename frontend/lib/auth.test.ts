import { beforeEach, describe, expect, it } from "vitest";

import { __resetSessionForTests, hasSession, markSignedIn, markSignedOut } from "./auth";

describe("signed-in hint", () => {
  beforeEach(() => {
    window.localStorage.clear();
    __resetSessionForTests();
  });

  it("starts signed out", () => {
    expect(hasSession()).toBe(false);
  });

  it("remembers a sign-in across reloads", () => {
    markSignedIn();
    __resetSessionForTests(); // a reload drops memory; storage keeps the hint

    expect(hasSession()).toBe(true);
  });

  it("forgets it on sign-out", () => {
    markSignedIn();
    markSignedOut();

    expect(hasSession()).toBe(false);
  });

  it("never keeps the hint in a way that holds a credential", () => {
    markSignedIn();

    expect(window.localStorage.getItem("dibs.signed-in")).toBe("1");
  });

  it("removes a token left by the old localStorage sessions", () => {
    window.localStorage.setItem("dibs.session-token", "old-bearer-token");

    expect(hasSession()).toBe(false);
    expect(window.localStorage.getItem("dibs.session-token")).toBeNull();
  });
});
