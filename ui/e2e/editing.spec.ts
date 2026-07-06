import { expect, test, type Page } from "@playwright/test";

// S4/M4: the editing write path (FR-1003 + FR-1002/FR-1006 edit
// halves) against the real server + built bundle. Every edit goes
// through debounced autosave → PUT /api/flows → merge → canonical
// emit; assertions read the SAVED state back through the API, not the
// DOM alone.

async function waitSaved(page: Page) {
  // autosave debounce is 1s; "saved" only renders after the PUT lands
  await expect(page.getByTestId("save-status")).toHaveAttribute(
    "data-state",
    "clean",
    { timeout: 10_000 },
  );
}

async function flowModel(page: Page, identity: string) {
  const response = await page.request.get(`/api/flows/${identity}`);
  expect(response.ok()).toBeTruthy();
  return response.json();
}

test.describe.configure({ mode: "serial" }); // shared workspace state

test("dragging a node autosaves a layout-only change", async ({ page }) => {
  await page.goto("/flows/main");
  const before = await flowModel(page, "flows/main");

  const node = page.getByTestId("node-start");
  await node.hover();
  await page.mouse.down();
  await page.mouse.move(500, 400, { steps: 5 });
  await page.mouse.up();
  await waitSaved(page);

  const after = await flowModel(page, "flows/main");
  expect(after.flow.layout.start).not.toEqual(before.flow.layout.start);
  expect(after.flow.nodes).toEqual(before.flow.nodes); // layout ONLY
  expect(after.flow.edges).toEqual(before.flow.edges);
  expect(after.etag).not.toBe(before.etag);
});

test("adding a node from the palette persists it", async ({ page }) => {
  await page.goto("/flows/main");
  await page.getByTestId("add-node").click();
  await page.getByTestId("palette-log").click();
  await expect(page.getByTestId("node-log")).toBeVisible();
  await waitSaved(page);

  const model = await flowModel(page, "flows/main");
  const added = model.flow.nodes.find((n: { id: string }) => n.id === "log");
  expect(added).toMatchObject({ id: "log", type: "log" });
  expect(model.flow.layout.log).toBeDefined();
});

test("config form edits a node's config and autosaves", async ({ page }) => {
  await page.goto("/flows/main");
  await page.getByTestId("node-log").click();
  await page.getByTestId("config-label").fill("hello from e2e");
  await page.getByTestId("config-label").blur();
  await waitSaved(page);

  const model = await flowModel(page, "flows/main");
  const log = model.flow.nodes.find((n: { id: string }) => n.id === "log");
  expect(log.config).toEqual({ label: "hello from e2e" });
});

test("connecting onto a wired input replaces the edge (E004)", async ({
  page,
}) => {
  await page.goto("/flows/main");
  // main ships start.out → end.done; rewire log.out → end.done
  const source = page
    .getByTestId("node-log")
    .locator(".react-flow__handle-right");
  const target = page
    .getByTestId("node-end")
    .locator(".react-flow__handle-left");
  await source.hover();
  await page.mouse.down();
  await target.hover();
  await page.mouse.up();
  await waitSaved(page);

  const model = await flowModel(page, "flows/main");
  const into = model.flow.edges.filter(
    (e: { to: string }) => e.to === "end.done",
  );
  expect(into).toEqual([{ from: "log.out", to: "end.done" }]); // ONE edge
});

test("deleting a node removes it and its edges", async ({ page }) => {
  await page.goto("/flows/main");
  await page.getByTestId("node-log").click();
  await page.getByTestId("delete-node").click();
  await expect(page.getByTestId("node-log")).toHaveCount(0);
  await waitSaved(page);

  const model = await flowModel(page, "flows/main");
  expect(
    model.flow.nodes.some((n: { id: string }) => n.id === "log"),
  ).toBeFalsy();
  expect(
    model.flow.edges.some((e: { from: string }) => e.from.startsWith("log.")),
  ).toBeFalsy();
});

test("start/end port editors edit flow inputs and outputs (FR-1006)", async ({
  page,
}) => {
  await page.goto("/flows/main");

  await page.getByTestId("node-start").click();
  await page.getByTestId("start-port-add").click();
  await page.getByTestId("start-port-name-0").fill("greeting");
  await page.getByTestId("start-port-type-0").selectOption("string");
  await waitSaved(page);

  await page.getByTestId("node-end").click();
  await page.getByTestId("end-port-required-0").uncheck();
  await waitSaved(page);

  const model = await flowModel(page, "flows/main");
  const start = model.flow.nodes.find(
    (n: { type: string }) => n.type === "start",
  );
  expect(start.config.ports).toEqual([{ name: "greeting", type: "string" }]);
  const end = model.flow.nodes.find((n: { type: string }) => n.type === "end");
  expect(end.config.ports).toEqual([{ name: "done", required: false }]);
});

test("nodes.py editor round-trips code and reports syntax errors", async ({
  page,
}) => {
  await page.goto("/flows/smoke");
  await page.getByTestId("open-code").click();
  const editor = page.getByTestId("code-text");
  await expect(editor).toHaveValue(/def summarize/);

  // break it: still SAVES (last-write-wins), error surfaces inline
  await editor.fill("def broken(:\n");
  await expect(page.getByTestId("code-save-status")).toHaveAttribute(
    "data-state",
    "clean",
    { timeout: 10_000 },
  );
  await expect(page.getByTestId("code-syntax-error")).toBeVisible();
  const broken = await page.request.get("/api/code/flows/smoke");
  expect((await broken.json()).syntax_error).not.toBeNull();

  // fix it back
  await editor.fill("def summarize(users):\n    return {'n': len(users)}\n");
  await expect(page.getByTestId("code-syntax-error")).toHaveCount(0, {
    timeout: 10_000,
  });
  await page.getByTestId("code-close").click();
});

test("external file change while clean live-reloads the canvas", async ({
  page,
}) => {
  await page.goto("/flows/main");
  await waitSaved(page);
  const before = await flowModel(page, "flows/main");

  // simulate a git checkout / hand edit: PUT with force from outside
  const moved = JSON.parse(JSON.stringify(before.flow));
  moved.layout[Object.keys(moved.layout)[0]] = [640, 480];
  const external = await page.request.put("/api/flows/flows/main", {
    data: { flow: moved, force: true },
  });
  expect(external.ok()).toBeTruthy();

  // the etag poll (2s) picks it up and reloads WITHOUT a conflict
  // prompt — the canvas was clean, so it's a silent live-reload
  await page.waitForTimeout(3_000); // > ETAG_POLL_MS
  await expect(page.getByTestId("save-conflict")).toHaveCount(0);
  await waitSaved(page);
});
