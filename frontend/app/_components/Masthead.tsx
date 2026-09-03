/**
 * The shared top bar — brand mark on the left, section nav on the right.
 * Pure and server-rendered; `current` marks the active tab so both the prompt
 * page and the watch dashboard stay in sync without duplicating the markup.
 */

import { AccountBadge } from "./AccountBadge";
import { ScopeMark } from "./Scope";

export function Masthead({ current }: { current: "new" | "watches" | "account" }) {
  return (
    <header className="masthead">
      <div className="brand">
        <ScopeMark />
        <span className="brand-name">DIBS</span>
        <span className="brand-tag">a reservation sniper</span>
      </div>
      <nav className="nav">
        <a href="/" aria-current={current === "new" ? "page" : undefined}>
          NEW WATCH
        </a>
        <a
          href="/watches"
          aria-current={current === "watches" ? "page" : undefined}
        >
          YOUR WATCHES
        </a>
        <AccountBadge />
      </nav>
    </header>
  );
}
