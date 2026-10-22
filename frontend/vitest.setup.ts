import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// Node 25+ ships its own `localStorage`/`sessionStorage` globals (undefined
// unless started with --localstorage-file). Vitest won't overwrite globals
// Node already defines, so point them at jsdom's working storage explicitly.
const dom = (globalThis as { jsdom?: { window: Window } }).jsdom;
if (dom) {
  for (const key of ["localStorage", "sessionStorage"] as const) {
    Object.defineProperty(globalThis, key, {
      value: dom.window[key],
      configurable: true,
      writable: true,
    });
  }
}

// Unmount anything rendered by @testing-library/react between tests so the
// jsdom document starts clean each time.
afterEach(() => {
  cleanup();
});
