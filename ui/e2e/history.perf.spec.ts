import { expect, test } from "@playwright/test";

const CASES = [
  { label: "10MB", runId: "19700102-000000-100000" },
  { label: "100MB", runId: "19700103-000000-100000" },
] as const;

for (const { label, runId } of CASES) {
  test(`history first render and retained heap: ${label}`, async ({ page }) => {
    await page.goto("/flows/perf");
    await expect(page.getByTestId("node-start")).toBeVisible();
    await page.getByTestId("open-history").click();
    const row = page.getByTestId("history-run").filter({ hasText: runId });
    await expect(row).toBeVisible();

    const cdp = await page.context().newCDPSession(page);
    await cdp.send("HeapProfiler.enable");
    await cdp.send("HeapProfiler.collectGarbage");
    const before = await cdp.send("Runtime.getHeapUsage");

    const started = performance.now();
    await row.click();
    await expect(page.getByTestId("run-state")).toHaveAttribute(
      "data-state",
      "passed",
      { timeout: 240_000 },
    );
    const renderMs = performance.now() - started;

    // MAX_ROWS bounds DOM rows, but v0.1 still holds every parsed record;
    // collect first so this reports retained state rather than fetch noise.
    await cdp.send("HeapProfiler.collectGarbage");
    const after = await cdp.send("Runtime.getHeapUsage");
    const retainedMb = (after.usedSize - before.usedSize) / 1_000_000;
    console.log(
      `[perf] browser history ${label}: first render ${renderMs.toFixed(0)}ms, ` +
        `retained JS heap delta ${retainedMb.toFixed(1)}MB`,
    );

    await expect(page.getByTestId("run-event")).toHaveCount(500);
    await cdp.detach();
  });
}
