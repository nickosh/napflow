import type { Page } from "@playwright/test";

/** F1 chrome: the flow list lives behind the top-left flows toggle. */
export async function openFlowsMenu(page: Page) {
  const toggle = page.getByTestId("flows-toggle");
  if ((await page.getByTestId("flow-list").count()) === 0) {
    await toggle.click();
  }
}

/** Navigate to a flow through the flows menu (the old sidebar click). */
export async function pickFlow(page: Page, identity: string) {
  await openFlowsMenu(page);
  await page
    .getByTestId("flow-list")
    .getByRole("button", { name: identity })
    .click();
}
