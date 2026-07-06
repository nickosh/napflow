import { expect, test } from "@playwright/test";

// S4/M2 smoke: the wheel-shipped path end to end — static bundle at /,
// data from the REST API, xyflow canvas rendering. The suite grows
// per milestone from here (owner call, PLAN S4).

test("served bundle renders the canvas with real workspace data", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page).toHaveTitle(/napflow/);
  // header shows the workspace name fetched from /api/workspace
  await expect(page.getByTestId("workspace-name")).toContainText("napf-e2e");
  // the canvas is a real xyflow instance…
  await expect(page.locator(".react-flow")).toBeVisible();
  // …showing one node per flow discovered by the real `napf init`
  for (const identity of ["flows/main", "flows/example", "flows/smoke"]) {
    await expect(page.getByText(identity)).toBeVisible();
  }
});

test("unknown client-side routes fall back to the app (SPA)", async ({
  page,
}) => {
  await page.goto("/flows/some/future/route");
  await expect(page.locator(".react-flow")).toBeVisible();
});
