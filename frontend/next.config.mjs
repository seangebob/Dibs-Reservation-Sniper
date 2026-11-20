import { PHASE_DEVELOPMENT_SERVER } from "next/constants.js";

/**
 * Where `next dev` forwards `/api/*` when NEXT_PUBLIC_API_BASE_URL is unset:
 * the backend's default local address.
 */
const DEV_API_URL = "http://localhost:8000";

/**
 * In `next dev` with no NEXT_PUBLIC_API_BASE_URL, the browser calls the API
 * through this server: `apiBaseUrl()` is empty, so requests go to
 * same-origin `/api/*`, which is forwarded to the backend. The browser never
 * makes a cross-origin request, so the backend needs no FRONTEND_ORIGINS for
 * local development, and a phone on the same Wi-Fi reaches the API through
 * this server too. Builds that set NEXT_PUBLIC_API_BASE_URL -- Docker and
 * production -- call the API directly over CORS, as before.
 *
 * @param {string} phase
 * @returns {import('next').NextConfig}
 */
export default function nextConfig(phase) {
  const forwardApi =
    phase === PHASE_DEVELOPMENT_SERVER &&
    !process.env.NEXT_PUBLIC_API_BASE_URL?.trim();

  return {
    reactStrictMode: true,
    // Emit a self-contained server (`.next/standalone/server.js`) plus a trimmed
    // node_modules so the production Docker image stays small. No effect on
    // `next dev` / `next start`; the local build just also writes the standalone
    // folder the Dockerfile copies.
    output: "standalone",
    ...(forwardApi && {
      async rewrites() {
        return [{ source: "/api/:path*", destination: `${DEV_API_URL}/api/:path*` }];
      },
      experimental: {
        // The forward gives up after 30s by default; a local model may take up
        // to the backend's 120s timeout on a cold start.
        proxyTimeout: 130_000,
      },
    }),
  };
}
