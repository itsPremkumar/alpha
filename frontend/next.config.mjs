import path from "node:path";
import { fileURLToPath } from "node:url";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// The Gateway API runs on port 8001 by default (see start.ps1 -GatewayPort).
// Override with ALPHA_INTERNAL_GATEWAY_BASE_URL when the backend lives elsewhere.
const gatewayBase = (process.env.ALPHA_INTERNAL_GATEWAY_BASE_URL || "http://127.0.0.1:8001").replace(/\/+$/, "");

/**
 * The dev server and the production build get SEPARATE output directories.
 *
 * `next dev` deletes everything under its `distDir` (except `cache/`) when it
 * starts - measured in this Next build at
 * `dist/esm/server/dev/hot-reloader-webpack.js:581` calling `clean()`, which is
 * `recursiveDelete(join(dir, distDir), /^cache/)`. Sharing one directory
 * therefore means starting the dev server DESTROYS the production build, and
 * then two things go wrong with nothing to indicate why:
 *
 *   - `start.ps1:629` and `scripts/watchdog.ps1:436` both gate the production
 *     path on `Test-Path frontend\.next\BUILD_ID`. Once a dev run has deleted
 *     it, every boot silently falls back to `next dev` and its cold compile.
 *   - The next `next build` then consumes dev-written Pages Router bookkeeping
 *     and dies in "Collecting page data" with
 *     `PageNotFoundError: Cannot find module for page: /_document`.
 *
 * `.next/dev` rather than a sibling like `.next-dev` on purpose: the shipped
 * `frontend/.gitignore` already ignores `.next/` at any depth, so the split
 * cannot leave an untracked build tree behind, and `tsconfig.json`'s existing
 * `.next/types` include still covers the dev server's generated types.
 *
 * The production path deliberately stays exactly `.next` - that path is the
 * contract `start.ps1` and `scripts/watchdog.ps1` read.
 */
const DEV_DIST_DIR = ".next/dev";
const PROD_DIST_DIR = ".next";

/** @type {(phase: string) => import('next').NextConfig} */
export default function nextConfig(phase) {
  return {
    reactStrictMode: true,
    devIndicators: false,
    // The dev server owns its own directory; every other phase (production
    // build, production server, export, typegen) shares the build directory.
    distDir: phase === PHASE_DEVELOPMENT_SERVER ? DEV_DIST_DIR : PROD_DIST_DIR,
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
}
