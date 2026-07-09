import { expect, test } from "@playwright/test";

// S4/M5 (FR-1005): run on canvas + history. Runs are REAL — the
// server executes the flow, events stream over the WebSocket, the
// overlay repaints from them. Specs assert the STATE driving the
// animations (data-run-status, chips, live log text) — the keyframes
// on top are cosmetic. Flows RUN here are owned by this spec only
// (parallel spec files share the workspace; editing.spec mutates
// flows/smoke, so we run its pristine copy flows/passcase instead):
// passcase passes offline out of the box; flows/failcase fails on
// defaults, passes on threshold=3; flows/slow aborts mid-delay.

test("live run: passed overlay, wire detail, run mode locks editing", async ({
  page,
}) => {
  await page.goto("/flows/passcase");
  await expect(page.getByTestId("node-verify")).toBeVisible();
  // editing surfaces are up before the run
  await expect(page.getByTestId("add-node")).toBeVisible();
  await expect(page.getByTestId("inspector")).toBeVisible();

  // smoke declares no Start ports → the hybrid popover skips itself
  await page.getByTestId("run-button").click();
  await expect(page.getByTestId("run-panel")).toBeVisible();
  await expect(page.getByTestId("run-state")).toHaveAttribute(
    "data-state",
    "passed",
  );

  // per-node outcome painted on the canvas (fixture→python→assert)
  for (const node of ["users", "summarize", "verify"]) {
    await expect(page.getByTestId(`node-${node}`)).toHaveAttribute(
      "data-run-status",
      "ok",
    );
  }
  await expect(page.getByTestId("run-asserts")).toContainText("2✓");

  // run mode locks editing: the palette goes away and the edit
  // inspector gives way to the RUN inspector (M5.5)
  await expect(page.getByTestId("open-code")).toBeVisible(); // header intact
  await expect(page.getByTestId("add-node")).toHaveCount(0);
  await expect(page.getByTestId("inspector")).toHaveCount(0);
  await expect(page.getByTestId("run-inspector")).toBeVisible();

  // full wire detail: expand the assert_result event row
  const assertRow = page
    .locator('[data-testid=run-event][data-event=assert_result]')
    .first();
  await assertRow.locator("div").first().click();
  await expect(page.getByTestId("run-event-detail").first()).toContainText(
    '"passed": true',
  );

  // node click filters the stream to that node's events AND fills the
  // run inspector with the node's run data (M5.5)
  await page.getByTestId("node-verify").click();
  await expect(page.getByTestId("run-filter")).toContainText("verify");
  await expect(page.getByTestId("run-inspector-status")).toContainText(
    "fired ×1",
  );
  for (const row of await page.getByTestId("run-event").all()) {
    await expect(row).toContainText("verify");
  }

  // leaving run mode restores the editing surfaces
  await page.getByTestId("exit-run").click();
  await expect(page.getByTestId("run-panel")).toHaveCount(0);
  await expect(page.getByTestId("inspector")).toBeVisible();
});

test("failing run: red assert, live log value, travelled wires", async ({
  page,
}) => {
  await page.goto("/flows/failcase");
  await expect(page.getByTestId("node-verify")).toBeVisible();

  // failcase declares a Start port → the popover opens, prefilled
  await page.getByTestId("run-button").click();
  await expect(page.getByTestId("run-input-threshold")).toHaveValue("100");
  await page.getByTestId("run-popover-start").click();

  await expect(page.getByTestId("run-state")).toHaveAttribute(
    "data-state",
    "failed",
  );
  await expect(page.getByTestId("node-verify")).toHaveAttribute(
    "data-run-status",
    "failed",
  );
  // the log node is alive: latest logged value rendered on the node
  await expect(page.getByTestId("node-log-value")).toContainText("100");
  // wires that carried messages show their travel dots
  expect(await page.getByTestId("edge-dot").count()).toBeGreaterThan(0);

  // M5.5 port traffic painting: the port that carried data is marked,
  // its tooltip holds the last value that crossed
  const echoOut = page.getByTestId("port-echo-output-out");
  await expect(echoOut).toHaveAttribute("data-carried", "true");
  await expect(echoOut).toHaveAttribute("title", /100/);

  // M5.5 wire click → the messages that traversed it
  await page
    .locator('.react-flow__edge[data-id="echo.out→verify.in"]')
    .click();
  await expect(page.getByTestId("traffic-filter")).toContainText("echo.out");
  await expect(page.getByTestId("run-message")).toHaveCount(1);
  await expect(page.getByTestId("run-message")).toContainText("100");

  // …and the port-handle twin of the same view (E004: same wire)
  await page.getByTestId("port-verify-input-in").click();
  await expect(page.getByTestId("traffic-filter")).toContainText("verify.in");
  await expect(page.getByTestId("run-message")).toHaveCount(1);

  // M5.5 run inspector: node click swaps the message list for the
  // node's run data — log history included (the append ring)
  await page.getByTestId("node-echo").click();
  await expect(page.getByTestId("traffic-filter")).toHaveCount(0);
  await expect(page.getByTestId("run-inspector-log")).toContainText("100");
  await expect(page.getByTestId("run-inspector-port").first()).toBeVisible();

  await page.getByTestId("exit-run").click();
});

test("run inputs override the Start-port default (napf run -i parity)", async ({
  page,
}) => {
  await page.goto("/flows/failcase");
  await page.getByTestId("run-button").click();
  await page.getByTestId("run-input-threshold").fill("3");
  await page.getByTestId("run-popover-start").click();

  await expect(page.getByTestId("run-state")).toHaveAttribute(
    "data-state",
    "passed",
  );
  await expect(page.getByTestId("node-verify")).toHaveAttribute(
    "data-run-status",
    "ok",
  );
  await page.getByTestId("exit-run").click();
});

test("a live run pulses and can be aborted", async ({ page }) => {
  await page.goto("/flows/slow");
  await page.getByTestId("run-button").click();

  // mid-delay: the run is live, the delay node is visibly executing
  await expect(page.getByTestId("run-state")).toHaveAttribute(
    "data-state",
    "running",
  );
  await expect(page.getByTestId("node-wait")).toHaveAttribute(
    "data-run-status",
    "active",
  );
  await expect(page.getByTestId("abort-run")).toBeVisible();

  // the follow toggle holds its pressed state; clicking releases and
  // re-engages it (scroll-driven auto-release needs an overflowing
  // list — covered by hand, the fixtures finish too small)
  const follow = page.getByTestId("follow-toggle");
  await expect(follow).toHaveAttribute("aria-pressed", "true");
  await follow.click();
  await expect(follow).toHaveAttribute("aria-pressed", "false");
  await follow.click();
  await expect(follow).toHaveAttribute("aria-pressed", "true");

  await page.getByTestId("abort-run").click();
  // run_finished(aborted) arrives over the same socket
  await expect(page.getByTestId("run-state")).toHaveAttribute(
    "data-state",
    "aborted",
  );
  await expect(page.getByTestId("abort-run")).toHaveCount(0); // not live
  await page.getByTestId("exit-run").click();
});

test("history browser lists and replays runs; EC20 dangling start", async ({
  page,
}) => {
  await page.goto("/flows/failcase");
  await page.getByTestId("open-history").click();
  await expect(page.getByTestId("run-panel")).toBeVisible();

  // the two runs above + the truncated JSONL serve.mjs planted
  const rows = page.getByTestId("history-run");
  await expect
    .poll(() => rows.count()) // the list fetch is async
    .toBeGreaterThanOrEqual(3);

  // a finished run replays to its final overlay (replay = re-read the
  // JSONL, D13 — same reducer, no live socket)
  await rows
    .filter({ has: page.locator('[data-state="failed"]') })
    .first()
    .click();
  await expect(page.getByTestId("run-state")).toHaveAttribute(
    "data-state",
    "failed",
  );
  await expect(page.getByTestId("node-verify")).toHaveAttribute(
    "data-run-status",
    "failed",
  );

  // the run that died mid-request reads `incomplete`, settled (EC20)
  await page.getByTestId("tab-history").click();
  await rows
    .filter({ has: page.locator('[data-state="incomplete"]') })
    .first()
    .click();
  await expect(page.getByTestId("run-state")).toHaveAttribute(
    "data-state",
    "incomplete",
  );
  await expect(page.getByTestId("node-echo")).toHaveAttribute(
    "data-run-status",
    "none", // dangling request_started tolerated: not stuck "active"
  );
  await expect(page.getByTestId("run-event")).toHaveCount(3);
});
