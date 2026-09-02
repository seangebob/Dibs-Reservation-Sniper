/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Emit a self-contained server (`.next/standalone/server.js`) plus a trimmed
  // node_modules so the production Docker image stays small. No effect on
  // `next dev` / `next start`; the local build just also writes the standalone
  // folder the Dockerfile copies.
  output: "standalone",
};

export default nextConfig;
