import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Build output goes straight into the server package: the wheel
// force-includes that directory (NFR-03, pyproject `artifacts`), so a
// built wheel always carries the UI and users never need Node.
// Dev mode: `npm run dev` proxies /api + /ws to a running `napf ui`.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/napflow/server/static",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:6273",
      "/ws": { target: "ws://127.0.0.1:6273", ws: true },
    },
  },
  test: {
    include: ["src/**/*.test.ts", "harness-tests/**/*.test.ts"],
  },
});
