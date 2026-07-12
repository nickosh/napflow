import { defineConfig, devices } from "@playwright/test";

import { allocateLoopbackPort } from "./e2e/allocate-port";

// Opt-in M0 browser-history baseline. It uses the same built bundle and
// real server as ordinary e2e, but serve.mjs additionally plants 10MB and
// 100MB JSONL histories. Keep it out of the required correctness suite:
// this records before/after measurements; M7 decides the eventual gates.
const port = allocateLoopbackPort(process.env.NAPF_E2E_PERF_PORT);
process.env.NAPF_E2E_PERF_PORT = port;
const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.perf.spec.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 300_000,
  reporter: "list",
  use: {
    baseURL,
    trace: "off",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "node e2e/serve.mjs",
    url: `${baseURL}/api/workspace`,
    reuseExistingServer: false,
    timeout: 120_000,
    gracefulShutdown: { signal: "SIGTERM", timeout: 10_000 },
    env: {
      ...process.env,
      NAPF_E2E_PORT: port,
      NAPF_E2E_PERF: "1",
    },
  },
});
