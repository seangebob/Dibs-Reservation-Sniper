import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// Unmount anything rendered by @testing-library/react between tests so the
// jsdom document starts clean each time.
afterEach(() => {
  cleanup();
});
