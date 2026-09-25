/** @type {import('next').NextConfig} */
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// The Gateway API runs on port 8001 by default (see start.ps1 -GatewayPort).
// Override with AGENT_WORKSPACE_INTERNAL_GATEWAY_BASE_URL when the backend lives elsewhere.
const gatewayBase = (process.env.AGENT_WORKSPACE_INTERNAL_GATEWAY_BASE_URL || "http://127.0.0.1:8001").replace(/\/+$/, "");

const nextConfig = {
  reactStrictMode: true,
  output: process.env.NEXT_CONFIG_BUILD_OUTPUT === "standalone" ? "standalone" : undefined,
  // Pin the tracing root to this app: a stray package-lock.json in an ancestor
  // folder (e.g. the Windows home dir) otherwise hijacks workspace inference.
  outputFileTracingRoot: __dirname,
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          {
            key: "Permissions-Policy",
            value: "microphone=(self), camera=(), geolocation=()",
          },
        ],
      },
    ];
  },
  async rewrites() {
    return [
      {
        source: "/api/gateway/enterprise/:path*",
        destination: `${gatewayBase}/api/gateway/enterprise/:path*`,
      },
      {
        source: "/api/gateway/api/commands/:path*",
        destination: `${gatewayBase}/api/gateway/api/commands/:path*`,
      },
      {
        source: "/api/gateway/:path*",
        destination: `${gatewayBase}/api/:path*`,
      },
      {
        source: "/api/:path*",
        destination: `${gatewayBase}/api/:path*`,
      },
    ];
  },
  webpack: (config) => {
    // Watchpack EINVAL on Windows system files (e.g. C:\pagefile.sys) when the
    // watcher resolves paths near the drive root — exclude them explicitly.
    config.watchOptions = {
      ...config.watchOptions,
      ignored: ["**/pagefile.sys", "**/hiberfil.sys", "**/swapfile.sys", "**/System Volume Information/**", "**/$RECYCLE.BIN/**"],
    };
    return config;
  },
};

export default nextConfig;
