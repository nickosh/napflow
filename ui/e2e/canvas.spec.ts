import { expect, test } from "@playwright/test";

// S4/M3: read-only canvas (FR-1002 render half + FR-1006 check half).
// The workspace is a real `napf init` plus flows/warn (W104) and
// flows/broken (E004) written by serve.mjs.

test("the manifest's main flow opens by default", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/flows\/main$/);
  await expect(page.getByTestId("node-start")).toBeVisible();
  await expect(page.getByTestId("node-end")).toBeVisible();
});

test("smoke flow renders its nodes, edges, and layout", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "flows/smoke" }).click();
  await expect(page).toHaveURL(/\/flows\/smoke$/);
  for (const id of ["start", "users", "summarize", "verify", "end"]) {
    await expect(page.getByTestId(`node-${id}`)).toBeVisible();
  }
  await expect(page.locator(".react-flow__edge")).toHaveCount(5);
});

test("deep links open the flow directly (SPA fallback)", async ({ page }) => {
  await page.goto("/flows/smoke");
  await expect(page.getByTestId("node-summarize")).toBeVisible();
});

test("inspector shows a node's config read-only on click", async ({
  page,
}) => {
  await page.goto("/flows/smoke");
  await page.getByTestId("node-summarize").click();
  const inspector = page.getByTestId("inspector");
  await expect(inspector).toContainText("summarize");
  await expect(inspector).toContainText("python");
  await expect(page.getByTestId("node-config")).toContainText("summarize");
});

test("check warnings surface on the canvas (W103 badge + panel)", async ({
  page,
}) => {
  await page.goto("/flows/warn");
  const req = page.getByTestId("node-req");
  await expect(req).toBeVisible();
  await expect(req.getByTestId("node-warnings")).toHaveText("1");
  await expect(page.getByTestId("diagnostics")).toContainText("W103");
});

test("a flow with check errors shows its E-codes instead of a canvas", async ({
  page,
}) => {
  await page.goto("/flows/broken");
  await expect(page.getByTestId("detail-error-diagnostics")).toContainText(
    "E004",
  );
  await expect(page.locator(".react-flow")).toHaveCount(0);
});
