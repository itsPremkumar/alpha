import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, devices } from "@playwright/test";

const frontendDir = path.dirname(fileURLToPath(import.meta.url));
const backendDir = path.resolve(frontendDir, "../backend");
const gatewayUrl = "http://127.0.0.1:8011";
const frontendUrl = "http://127.0.0.1:3011";

export default defineConfig({
  testDir: "./tests/e2e-real-backend",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: [["list"], ["html", { open: "never" }]],
  outputDir: "./test-results",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  use: {
    ...devices["Desktop Chrome"],
    baseURL: frontendUrl,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  webServer: [
    {
      command: "uv run python scripts/run_replay_gateway.py --port 8011 --cors http://127.0.0.1:3011",
      cwd: backendDir,
      url: `${gatewayUrl}/health/ready`,
      timeout: 180_000,
      reuseExistingServer: false,
      env: {
        ALPHA_AUTH_DISABLED: "1",
        ALPHA_ENABLE_TEST_SEED: "1",
      },
    },
    {
      command: "pnpm exec next dev -H 127.0.0.1 -p 3011",
      cwd: frontendDir,
      url: frontendUrl,
      timeout: 180_000,
      reuseExistingServer: false,
      env: {
        ALPHA_INTERNAL_GATEWAY_BASE_URL: gatewayUrl,
        SKIP_ENV_VALIDATION: "1",
      },
    },
  ],
});
