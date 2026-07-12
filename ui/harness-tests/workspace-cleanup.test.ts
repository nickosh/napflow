import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { createWorkspaceCleanup } from "../e2e/workspace-cleanup.mjs";

describe("Playwright workspace cleanup", () => {
  it("removes a generated workspace recursively and is idempotent", () => {
    const workspace = mkdtempSync(join(tmpdir(), "napf-cleanup-test-"));
    try {
      const nested = join(workspace, "flows", "demo");
      mkdirSync(nested, { recursive: true });
      writeFileSync(join(nested, "flow.yaml"), "schema: napflow/v1\n", "utf-8");

      const cleanup = createWorkspaceCleanup(workspace);
      expect(cleanup()).toBe(true);
      expect(existsSync(workspace)).toBe(false);
      expect(cleanup()).toBe(true);
    } finally {
      // Keep the test hermetic even if an assertion exposes a cleanup bug.
      rmSync(workspace, { recursive: true, force: true });
    }
  });

  it("retries after removal fails instead of marking the workspace clean", () => {
    let attempts = 0;
    const reports: unknown[] = [];
    const cleanup = createWorkspaceCleanup("locked-workspace", {
      remove: () => {
        attempts += 1;
        if (attempts === 1) throw new Error("directory is still in use");
      },
      report: (_message: string, error: unknown) => reports.push(error),
    });

    expect(cleanup()).toBe(false);
    expect(cleanup()).toBe(true);
    expect(cleanup()).toBe(true);
    expect(attempts).toBe(2);
    expect(reports).toHaveLength(1);
  });
});
