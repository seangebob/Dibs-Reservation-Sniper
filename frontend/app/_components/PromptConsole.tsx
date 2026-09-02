"use client";

/**
 * The interactive half of the prompt page: one input, one submit, and the
 * request state machine (idle → loading → result | error). It is the only
 * component here that holds state or calls the API; everything it shows goes
 * through the pure `ResultView`, and the input is locked while a request is in
 * flight so a prompt can never be double-fired (Requirement 1.6).
 */

import { useRef, useState } from "react";
import { parseAndBook } from "@/lib/api";
import { ResultView, type ConsoleState } from "./ResultView";

const PRESETS = [
  "watch Côte for 4 this Saturday 18:00–21:00",
  "book The Berlin for 6 on Thursday at 19:30",
  "brunch for 3 at Bhima's Warung on Sunday",
];

export function PromptConsole() {
  const [prompt, setPrompt] = useState("");
  const [state, setState] = useState<ConsoleState>({ phase: "idle" });
  const inputRef = useRef<HTMLInputElement>(null);

  const busy = state.phase === "loading";

  async function run(text: string) {
    const trimmed = text.trim();
    if (trimmed.length === 0 || busy) return;

    setState({ phase: "loading" });
    const result = await parseAndBook(trimmed);
    if (result.ok) {
      setState({ phase: "result", result: result.data });
    } else {
      setState({ phase: "error", message: result.message, status: result.status });
    }
  }

  function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    void run(prompt);
  }

  function applyPreset(text: string) {
    if (busy) return;
    setPrompt(text);
    inputRef.current?.focus();
  }

  return (
    <div className="console">
      <form className="field" data-busy={busy} onSubmit={onSubmit}>
        <span className="field-caret" aria-hidden="true">
          &gt;
        </span>
        <label htmlFor="prompt" className="sr-only">
          Describe the reservation to watch or book
        </label>
        <input
          id="prompt"
          ref={inputRef}
          type="text"
          autoComplete="off"
          spellCheck={false}
          placeholder="watch Côte for 4 this Saturday 18:00–21:00"
          value={prompt}
          disabled={busy}
          aria-busy={busy}
          onChange={(e) => setPrompt(e.target.value)}
        />
        <button
          type="submit"
          className="acquire"
          disabled={busy || prompt.trim().length === 0}
        >
          {busy ? "ACQUIRING…" : "ACQUIRE ▸"}
        </button>
      </form>

      <div className="presets">
        <span className="presets-label">PRESETS</span>
        {PRESETS.map((preset) => (
          <button
            key={preset}
            type="button"
            className="preset"
            disabled={busy}
            onClick={() => applyPreset(preset)}
          >
            {preset}
          </button>
        ))}
      </div>

      <ResultView state={state} onRetry={() => void run(prompt)} />
    </div>
  );
}
