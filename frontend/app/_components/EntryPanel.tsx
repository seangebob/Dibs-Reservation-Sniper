"use client";

/**
 * The front door's two ways in: describe the reservation in a prompt, or fill
 * in a form. The form needs no language model, so the site stays usable when
 * prompt parsing isn't configured. Both panels stay mounted, so switching tabs
 * never throws away a half-typed prompt or form.
 */

import { useId, useState } from "react";
import { PromptConsole } from "./PromptConsole";
import { WatchForm } from "./WatchForm";

type Tab = "prompt" | "form";

const TABS: { key: Tab; label: string }[] = [
  { key: "prompt", label: "DESCRIBE IT" },
  { key: "form", label: "FILL IN A FORM" },
];

export function EntryPanel() {
  const id = useId();
  const [tab, setTab] = useState<Tab>("prompt");

  return (
    <div className="entry">
      <div className="entry-tabs" role="tablist" aria-label="How to set up a watch">
        {TABS.map(({ key, label }) => (
          <button
            key={key}
            id={`${id}-tab-${key}`}
            type="button"
            role="tab"
            className="entry-tab"
            aria-selected={tab === key}
            aria-controls={`${id}-panel-${key}`}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>

      <div
        id={`${id}-panel-prompt`}
        role="tabpanel"
        aria-labelledby={`${id}-tab-prompt`}
        hidden={tab !== "prompt"}
      >
        <PromptConsole />
      </div>
      <div
        id={`${id}-panel-form`}
        role="tabpanel"
        aria-labelledby={`${id}-tab-form`}
        hidden={tab !== "form"}
      >
        <WatchForm />
      </div>
    </div>
  );
}
