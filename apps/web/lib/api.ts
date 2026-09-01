/**
 * The single boundary between the frontend and the Dibs backend.
 *
 * Every call goes through `apiFetch`, which attaches the anonymous client id
 * (Requirement 2.2), and normalizes ALL failures — network, non-2xx, malformed
 * body — into a discriminated `ApiResult`, so no page component ever handles a
 * raw fetch rejection or renders a raw error object (Requirement 1.7).
 */

import { getClientId } from "./client-id";
import type {
  AvailabilityQuery,
  PromptExecutionResult,
  Watch,
} from "@/types/api";

export type ApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; message: string; status?: number };

const DEFAULT_BASE_URL = "http://localhost:8000";

/**
 * The backend base URL. Reads `NEXT_PUBLIC_API_BASE_URL` (inlined at build
 * time) and falls back to localhost for zero-config local development.
 */
export function apiBaseUrl(): string {
  const configured = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
  return configured && configured.length > 0 ? configured : DEFAULT_BASE_URL;
}

async function errorMessage(response: Response): Promise<string> {
  // The backend serializes every handled error as `{ "detail": "..." }`.
  try {
    const body: unknown = await response.json();
    if (
      body &&
      typeof body === "object" &&
      "detail" in body &&
      typeof (body as { detail: unknown }).detail === "string"
    ) {
      return (body as { detail: string }).detail;
    }
  } catch {
    // fall through to a status-based default
  }
  if (response.status >= 500) {
    return "The server ran into a problem. Please try again in a moment.";
  }
  if (response.status === 404) {
    return "That could not be found.";
  }
  return "Something went wrong with that request.";
}

async function apiFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<ApiResult<T>> {
  const headers = new Headers(init?.headers);
  if (!headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const clientId = getClientId();
  if (clientId) {
    headers.set("X-Dibs-Client-Id", clientId);
  }

  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}${path}`, { ...init, headers });
  } catch {
    return {
      ok: false,
      message: "Couldn't reach the server. Check your connection and try again.",
    };
  }

  if (!response.ok) {
    return { ok: false, status: response.status, message: await errorMessage(response) };
  }

  if (response.status === 204) {
    return { ok: true, data: undefined as T };
  }
  try {
    return { ok: true, data: (await response.json()) as T };
  } catch {
    return {
      ok: false,
      status: response.status,
      message: "The server sent an unexpected response.",
    };
  }
}

/** POST a raw prompt for parsing, booking, or watch creation. */
export function parseAndBook(
  prompt: string,
): Promise<ApiResult<PromptExecutionResult>> {
  return apiFetch("/api/parse-and-book", {
    method: "POST",
    body: JSON.stringify({ prompt }),
  });
}

/** GET the calling client's own watches (owner-scoped, durable). */
export function listMyWatches(): Promise<ApiResult<Watch[]>> {
  return apiFetch("/api/watches/mine", { method: "GET" });
}

/** DELETE (cancel) a watch by id; returns its resolved final state. */
export function cancelWatch(watchId: string): Promise<ApiResult<Watch>> {
  return apiFetch(`/api/watches/${encodeURIComponent(watchId)}`, {
    method: "DELETE",
  });
}

/** POST a fully-validated query to open a watch directly. */
export function createWatch(
  query: AvailabilityQuery,
  autoBook = false,
): Promise<ApiResult<Watch>> {
  return apiFetch(`/api/watches?auto_book=${autoBook ? "true" : "false"}`, {
    method: "POST",
    body: JSON.stringify(query),
  });
}
