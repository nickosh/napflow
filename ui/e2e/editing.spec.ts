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

test("dragging a type from the palette adds it at the drop point", async ({
  page,
}) => {
  await page.goto("/flows/main");
  await page.getByTestId("add-node").click();
  // HTML5 dnd: Playwright mouse moves don't carry dataTransfer, so
  // dispatch the drag events by hand with a shared store
  const dataTransfer = await page.evaluateHandle(() => new DataTransfer());
  await page
    .getByTestId("palette-note")
    .dispatchEvent("dragstart", { dataTransfer });
  const pane = page.locator(".react-flow__pane");
  const box = (await pane.boundingBox())!;
  const at = { x: box.x + box.width - 80, y: box.y + box.height - 60 };
  await pane.dispatchEvent("dragover", {
    dataTransfer,
    clientX: at.x,
    clientY: at.y,
  });
  await pane.dispatchEvent("drop", {
    dataTransfer,
    clientX: at.x,
    clientY: at.y,
  });

  await expect(page.getByTestId("node-note")).toBeVisible();
  await waitSaved(page);
  const model = await flowModel(page, "flows/main");
  expect(
    model.flow.nodes.some(
      (n: { id: string; type: string }) => n.id === "note" && n.type === "note",
    ),
  ).toBeTruthy();
  expect(model.flow.layout.note).toBeDefined();

  // cleanup: this suite is serial over one workspace — later tests
  // assert exact edge/node sets on flows/main
  await page.getByTestId("node-note").click();
  await page.getByTestId("delete-node").click();
  await waitSaved(page);
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

test("assert checks edit as structured rows", async ({ page }) => {
  await page.goto("/flows/smoke");
  await page.getByTestId("node-verify").click();
  // smoke ships two expr checks; edit the first one's value: 0 → 5
  await expect(page.getByTestId("check-expr-0")).toHaveValue(
    "trigger.value.total",
  );
  await page.getByTestId("check-value-0").fill("5");
  await page.getByTestId("check-value-0").blur();
  await waitSaved(page);

  let model = await flowModel(page, "flows/smoke");
  let verify = model.flow.nodes.find((n: { id: string }) => n.id === "verify");
  expect(verify.config.checks[0]).toEqual({
    kind: "expr",
    expr: "trigger.value.total",
    op: "gt",
    value: 5, // numeric text commits as a number, not "5"
  });

  // add a status check row, then remove it again
  await page.getByTestId("check-add").click();
  await page.getByTestId("check-kind-2").selectOption("status");
  await waitSaved(page);
  model = await flowModel(page, "flows/smoke");
  verify = model.flow.nodes.find((n: { id: string }) => n.id === "verify");
  expect(verify.config.checks[2]).toEqual({ kind: "status", equals: 200 });

  await page.getByTestId("check-remove-2").click();
  await page.getByTestId("check-value-0").fill("0");
  await page.getByTestId("check-value-0").blur();
  await waitSaved(page);
  model = await flowModel(page, "flows/smoke");
  verify = model.flow.nodes.find((n: { id: string }) => n.id === "verify");
  expect(verify.config.checks).toHaveLength(2);
  expect(verify.config.checks[0].value).toBe(0);
});

test("switch cases edit as structured rows", async ({ page }) => {
  await page.goto("/flows/main");
  await page.getByTestId("add-node").click();
  await page.getByTestId("palette-switch").click();
  await expect(page.getByTestId("node-switch")).toBeVisible();
  await page.getByTestId("node-switch").click();

  await page.getByTestId("config-expr").fill("trigger.value.state");
  await page.getByTestId("case-name-0").fill("ready");
  await page.getByTestId("case-equals-0").fill("READY");
  await page.getByTestId("case-equals-0").blur();
  await page.getByTestId("case-add").click();
  await page.getByTestId("case-name-1").fill("count");
  await page.getByTestId("case-equals-1").fill("3");
  await page.getByTestId("case-equals-1").blur();
  await waitSaved(page);

  const model = await flowModel(page, "flows/main");
  const sw = model.flow.nodes.find((n: { id: string }) => n.id === "switch");
  expect(sw.config).toEqual({
    expr: "trigger.value.state",
    cases: [
      { name: "ready", equals: "READY" }, // bare word stays a string
      { name: "count", equals: 3 }, // numeric text commits native
    ],
  });
  // the last case row can't be removed (model min_length=1)
  await expect(page.getByTestId("case-remove-1")).toBeEnabled();
  await expect(page.getByTestId("case-remove-0")).toBeEnabled();
  await page.getByTestId("case-remove-1").click();
  await expect(page.getByTestId("case-remove-0")).toBeDisabled();

  // cleanup for the serial suite
  await page.getByTestId("delete-node").click();
  await waitSaved(page);
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

test("start-port defaults save with the declared type, not as strings", async ({
  page,
}) => {
  await page.goto("/flows/main");
  await page.getByTestId("node-start").click();

  // the previous test left port 0 (greeting: string); add a number port
  await page.getByTestId("start-port-add").click();
  await page.getByTestId("start-port-name-1").fill("retries");
  await page.getByTestId("start-port-type-1").selectOption("number");
  const cell = page.getByTestId("start-port-default-1");

  // a non-numeric default stays local (red border), never saves
  await cell.fill("not-a-number");
  await cell.blur();
  await expect(cell).toHaveCSS("border-color", "rgb(198, 40, 40)");

  await cell.fill("42");
  await cell.blur();
  await waitSaved(page);

  const model = await flowModel(page, "flows/main");
  const start = model.flow.nodes.find(
    (n: { type: string }) => n.type === "start",
  );
  expect(start.config.ports[1]).toEqual({
    name: "retries",
    type: "number",
    default: 42, // native number in the YAML, not "42"
  });

  // cleanup for the serial suite
  await page.getByTestId("start-port-remove-1").click();
  await waitSaved(page);
});

test("nodes.py editor round-trips code and reports syntax errors", async ({
  page,
}) => {
  await page.goto("/flows/smoke");
  await page.getByTestId("open-code").click();
  // CodeMirror pane (bundled, no CDN): .cm-content is a real
  // contenteditable, so plain keyboard input drives it
  const editor = page.getByTestId("code-text");
  await expect(editor).toContainText("def summarize", { timeout: 15_000 });
  const retype = async (code: string) => {
    await editor.locator(".cm-content").click();
    await page.keyboard.press("ControlOrMeta+a");
    await page.keyboard.insertText(code);
  };

  // break it: still SAVES (last-write-wins), error surfaces inline
  await retype("def broken(:\n");
  await expect(page.getByTestId("code-save-status")).toHaveAttribute(
    "data-state",
    "clean",
    { timeout: 10_000 },
  );
  await expect(page.getByTestId("code-syntax-error")).toBeVisible();
  const broken = await page.request.get("/api/code/flows/smoke");
  expect((await broken.json()).syntax_error).not.toBeNull();

  // fix it back
  await retype("def summarize(users):\n    return {'n': len(users)}\n");
  await expect(page.getByTestId("code-syntax-error")).toHaveCount(0, {
    timeout: 10_000,
  });
  await page.getByTestId("code-close").click();
});

test("dragging a mismatched wire shows the live W102 hint (never blocks)", async ({
  page,
}) => {
  // flows/hint has a subflow whose `count` input is number-typed;
  // start.out is object — hovering it mid-drag must raise the hint
  await page.goto("/flows/hint");
  const source = page
    .getByTestId("node-start")
    .locator(".react-flow__handle-right");
  const target = page
    .getByTestId("node-sub")
    .locator('[data-handleid="count"]');
  await source.hover();
  await page.mouse.down();
  await target.hover();
  await expect(page.getByTestId("connect-hint")).toContainText("W102");
  await expect(page.getByTestId("connect-hint")).toContainText("number");

  // soft types: releasing still CONNECTS, and the checker agrees post-save
  await page.mouse.up();
  await waitSaved(page);
  const model = await flowModel(page, "flows/hint");
  expect(model.flow.edges).toContainEqual({
    from: "start.out",
    to: "sub.count",
  });
  expect(
    model.diagnostics.some((d: { code: string }) => d.code === "W102"),
  ).toBeTruthy();
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
