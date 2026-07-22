import { expect, test } from "@playwright/test";

import { openFlowsMenu, pickFlow } from "./helpers";

// S4/M3: read-only canvas (FR-1002 render half + FR-1006 check half).
// The workspace is a real `napf init` plus flows/warn (W103) and
// flows/broken (E004) written by serve.mjs.

test("the manifest's main flow opens by default", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/flow\/flows\/main$/);
  await expect(page.getByTestId("node-start")).toBeVisible();
  await expect(page.getByTestId("node-end")).toBeVisible();
});

test("smoke flow renders its nodes, edges, and layout", async ({ page }) => {
  await page.goto("/");
  await pickFlow(page, "flows/smoke");
  await expect(page).toHaveURL(/\/flow\/flows\/smoke$/);
  for (const id of ["start", "users", "summarize", "verify", "end"]) {
    await expect(page.getByTestId(`node-${id}`)).toBeVisible();
  }
  await expect(page.locator(".react-flow__edge")).toHaveCount(5);
});

test("deep links open the flow directly (SPA fallback)", async ({ page }) => {
  await page.goto("/flow/flows/smoke");
  await expect(page.getByTestId("node-summarize")).toBeVisible();
});

test("reserved-character flow identities round-trip through deep links", async ({
  page,
}) => {
  const encodedIdentity = "flows/encoded%20name%20%23100%25";
  await page.goto(`/flow/${encodedIdentity}`);
  await expect(page.getByTestId("workspace-name")).toHaveText(/^napf-e2e-/);
  await openFlowsMenu(page);
  await expect(
    page.getByRole("button", { name: "flows/encoded name #100%" }),
  ).toBeVisible();
  await expect(page).toHaveURL(
    /\/flow\/flows\/encoded%20name%20%23100%25$/,
  );

  const detail = await page.evaluate(async (identityPath) => {
    const response = await fetch(`/api/flows/${identityPath}`);
    return { ok: response.ok, body: await response.json() };
  }, encodedIdentity);
  expect(detail.ok, JSON.stringify(detail.body)).toBeTruthy();
  expect(detail.body.identity).toBe("flows/encoded name #100%");
});

test("namespaced deep links do not collide with API or static routes", async ({
  page,
}) => {
  for (const identity of ["api/workspace", "assets/canvas"]) {
    await page.goto(`/flow/${identity}`);
    await expect(page).toHaveURL(new RegExp(`/flow/${identity}$`));
    await expect(page.getByTestId("node-start")).toBeVisible();

    const detail = await page.request.get(`/api/flows/${identity}`);
    expect(detail.ok()).toBeTruthy();
    expect((await detail.json()).identity).toBe(identity);
  }
});

test("back-forward keeps an accepted missing target as the current route", async ({
  page,
}) => {
  await page.goto("/flow/flows/main");
  await expect(page.getByTestId("node-start")).toBeVisible();
  await page.evaluate(() => {
    const current = Number.isInteger(history.state?.napflowIndex)
      ? history.state.napflowIndex
      : 0;
    history.pushState(
      { ...(history.state ?? {}), napflowIndex: current + 1 },
      "",
      "/flow/flows/does/not/exist",
    );
  });

  await page.goBack();
  await expect(page).toHaveURL(/\/flow\/flows\/main$/);
  await page.goForward();
  await expect(page).toHaveURL(/\/flow\/flows\/does\/not\/exist$/);
  await expect(page.getByTestId("detail-error")).toBeVisible();
});

test("selecting a node opens its in-card editor (F1)", async ({ page }) => {
  await page.goto("/flow/flows/smoke");
  const node = page.getByTestId("node-summarize");
  await node.click();
  // the card header carries id + type; selection expands the editor
  await expect(node).toContainText("summarize");
  await expect(node).toContainText("python");
  await node.getByText("raw config").click();
  await expect(page.getByTestId("node-config")).toContainText("summarize");
});

test("check warnings surface on the canvas (W103 badge + console)", async ({
  page,
}) => {
  await page.goto("/flow/flows/warn");
  const req = page.getByTestId("node-req");
  await expect(req).toBeVisible();
  await expect(req.getByTestId("node-warnings")).toHaveText("1");
  // F1: diagnostics live in the console — its button carries the count
  await expect(page.getByTestId("console-toggle")).toContainText("1");
  await page.getByTestId("console-toggle").click();
  await expect(page.getByTestId("diagnostics")).toContainText("W103");
});

test("disconnected islands stay editable and W104 names every execution source", async ({
  page,
}) => {
  await page.goto("/flow/flows/island");
  const island = page.getByTestId("node-stranded");
  await expect(island).toBeVisible();
  await expect(island.getByTestId("node-warnings")).toHaveText("1");
  await page.getByTestId("console-toggle").click();
  await expect(page.getByTestId("diagnostics")).toContainText("W104");
  await expect(page.getByTestId("diagnostics")).toContainText(
    "unreachable from any execution source",
  );
});

test("a flow with check errors stays editable: canvas + E-codes (M4 pin)", async ({
  page,
}) => {
  await page.goto("/flow/flows/broken");
  // the canvas still renders — mid-edit invalid flows must stay editable
  await expect(page.getByTestId("node-start")).toBeVisible();
  await page.getByTestId("console-toggle").click();
  await expect(page.getByTestId("diagnostics")).toContainText("E004");
});

test("an unloadable flow shows its E-codes instead of a canvas", async ({
  page,
}) => {
  await page.goto("/flow/flows/unloadable");
  await expect(page.getByTestId("detail-error-diagnostics")).toContainText(
    "E002",
  );
  await expect(page.locator(".react-flow")).toHaveCount(0);
});
