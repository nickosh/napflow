import { expect, test } from "@playwright/test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { NODE_META, PICKER_TABS } from "../src/catalog";
import { NODE_TYPES } from "../src/forms";

function pathGeometry(markup: string): string[] {
  return [...markup.matchAll(/<path\b[^>]*\bd="([^"]+)"/g)].map(
    (match) => match[1],
  );
}

test("picker presentation follows NODE_META categories, descriptions, and icons", async ({
  page,
}) => {
  // passcase is read-only across the E2E suite; editing.spec intentionally
  // mutates flows/main in a parallel worker.
  await page.goto("/flow/flows/passcase");
  await page.getByTestId("add-node").click();

  for (const category of PICKER_TABS.slice(1)) {
    await page.getByRole("button", { name: category, exact: true }).click();
    const actual = await page
      .locator('button[data-testid^="palette-"]')
      .evaluateAll((rows) =>
        rows.map((row) => row.getAttribute("data-testid")!.slice("palette-".length)),
      );
    const expected = NODE_TYPES.filter(
      (type) => NODE_META[type].category === category,
    );
    expect(actual).toEqual(expected);
  }

  await page.getByRole("button", { name: "All", exact: true }).click();
  const requestRow = page.getByTestId("palette-request");
  await requestRow.hover();
  await expect(
    page.getByText(NODE_META.request.description, { exact: true }),
  ).toBeVisible();

  const expectedPaths = pathGeometry(
    renderToStaticMarkup(
      createElement(NODE_META.request.icon, {
        size: 15,
        color: "var(--accent)",
      }),
    ),
  );
  expect(expectedPaths.length).toBeGreaterThan(0);
  const actualPaths = await requestRow
    .locator("svg")
    .first()
    .locator("path")
    .evaluateAll((paths) => paths.map((path) => path.getAttribute("d")));
  expect(actualPaths).toEqual(expectedPaths);
});
