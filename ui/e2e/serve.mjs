// Playwright webServer command: scaffold a FRESH workspace (real
// `napf init`, so e2e always exercises the first-touch surface) and
// serve it with the real server + the BUILT bundle. Cross-platform —
// no shell-isms (NFR-02 applies to tooling too).
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const repo = resolve(import.meta.dirname, "..", "..");
const port = process.env.NAPF_E2E_PORT ?? "46273";

const workspace = mkdtempSync(join(tmpdir(), "napf-e2e-"));
const init = spawnSync(
  "uv",
  ["run", "--project", repo, "napf", "init", workspace],
  { stdio: "inherit" },
);
if (init.status !== 0) {
  process.exit(init.status ?? 1);
}

const server = spawn(
  "uv",
  [
    "run",
    "--project",
    repo,
    "napf",
    "ui",
    "--no-browser",
    "--port",
    port,
  ],
  { cwd: workspace, stdio: "inherit" },
);
server.on("exit", (code) => process.exit(code ?? 0));
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => server.kill(signal));
}
