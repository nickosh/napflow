import { expect, test } from "@playwright/test";

import { openFlowsMenu } from "./helpers";

// S4/M6: subflow UX (FR-1007, D09) — ghost-wires, drill-in, "used in
// N places", clone-to-new-flow. This spec OWNS flows/parent,
// flows/child, and flows/ghostcase (the clone test repoints
// parent.sub); no other spec may touch them. Tests run in file order
// (fullyParallel: false) — the mutating clone test goes LAST.

test("cross-node template refs render as ghost-wires", async ({ page }) => {
  await page.goto("/flow/flows/ghostcase");
  await expect(page.getByTestId("node-two")).toBeVisible();
  // 3 real wires + 1 ghost (two's label reads one's output)
  const ghost = page.locator(".react-flow__edge.napf-ghost-edge");
  await expect(ghost).toHaveCount(1);
  await expect(page.locator(".react-flow__edge")).toHaveCount(4);
});

test("double-clicking a flow node drills into its target", async ({
  page,
}) => {
  await page.goto("/flow/flows/parent");
  await page.getByTestId("node-sub").dblclick();
  await expect(page).toHaveURL(/\/flow\/flows\/child$/);
  // the child canvas replaces the parent's — pure navigation (D09)
  await expect(page.getByTestId("node-sub")).toHaveCount(0);
  await expect(page.getByTestId("node-start")).toBeVisible();
  // browser back returns to the parent (popstate)
  await page.goBack();
  await expect(page).toHaveURL(/\/flow\/flows\/parent$/);
  await expect(page.getByTestId("node-sub")).toBeVisible();
});

test("the drilled-in flow reports where it is used (D09)", async ({
  page,
}) => {
  await page.goto("/flow/flows/child");
  const usedBy = page.getByTestId("used-by");
  await expect(usedBy).toContainText("used in 1 place");
  await expect(usedBy).toContainText("(sub)");
  await page.getByTestId("used-by-flows/parent").click();
  await expect(page).toHaveURL(/\/flow\/flows\/parent$/);
});

test("the in-card editor opens a flow node's target", async ({ page }) => {
  await page.goto("/flow/flows/parent");
  await page.getByTestId("node-sub").click();
  await page.getByTestId("drill-in").click();
  await expect(page).toHaveURL(/\/flow\/flows\/child$/);
});

test("clone-to-new-flow forks the target and repoints the node", async ({
  page,
}) => {
  await page.goto("/flow/flows/parent");
  await page.getByTestId("node-sub").click();
  await page.getByTestId("clone-flow").click();
  // the dest prefills from the target; keep the default
  await expect(page.getByTestId("clone-dest")).toHaveValue(
    "flows/child_copy",
  );
  await page.getByTestId("clone-confirm").click();
  // the fork appears in the flows menu and THIS node now references it
  await openFlowsMenu(page);
  await expect(
    page
      .getByTestId("flow-list")
      .getByRole("button", { name: "flows/child_copy" }),
  ).toBeVisible();
  await page.getByTestId("flows-toggle").click(); // close the menu again
  await page.getByTestId("node-sub").getByText("raw config").click();
  await expect(page.getByTestId("node-config")).toContainText(
    "flows/child_copy",
  );
  // the repoint autosaves like any other edit
  await expect(page.getByTestId("save-status")).toHaveAttribute(
    "data-state",
    "clean",
  );
  // cloning onto an existing folder collides (409 surfaces inline);
  // the node now targets the copy, so flows/child is a plain collision
  await page.getByTestId("clone-flow").click();
  await page.getByTestId("clone-dest").fill("flows/child");
  await page.getByTestId("clone-confirm").click();
  await expect(page.getByTestId("clone-error")).toContainText(
    "already exists",
  );
});
