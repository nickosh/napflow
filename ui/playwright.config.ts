import { defineConfig, devices } from "@playwright/test";

// E2e runs against the BUILT bundle served by the real `napf ui`
// (serve.mjs scaffolds a fresh workspace) — the same path a user hits
// after `uv tool install napflow`. Run `npm run build` first.
const port = process.env.NAPF_E2E_PORT ?? "46273";
const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: "./e2e",
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
  },
});
