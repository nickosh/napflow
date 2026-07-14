import { defineConfig, devices } from "@playwright/test";

import { allocateLoopbackPort } from "./e2e/allocate-port";

// E2e runs against the BUILT bundle served by the real `napf ui`
// (serve.mjs scaffolds a fresh workspace) — the same path a user hits
// after `uv tool install napflow`. Run `npm run build` first.
const port = allocateLoopbackPort(process.env.NAPF_E2E_PORT);
// Playwright re-evaluates config in worker processes. Publishing the
// coordinator's allocation makes every worker reuse the same fresh port.
process.env.NAPF_E2E_PORT = port;
const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: "./e2e",
  testIgnore: "**/*.perf.spec.ts",
  fullyParallel: false, // one shared server + workspace
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL,
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "node e2e/serve.mjs",
    url: `${baseURL}/api/workspace`,
    reuseExistingServer: false,
    timeout: 120_000,
    gracefulShutdown: { signal: "SIGTERM", timeout: 10_000 },
    env: { ...process.env, NAPF_E2E_PORT: port },
  },
});
