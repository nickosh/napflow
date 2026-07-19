import { expect, test, type Page } from "@playwright/test";

import { pickFlow } from "./helpers";

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

async function expectNodeInsideCanvas(page: Page, testId: string) {
  await expect(page.getByTestId(testId)).toBeVisible();
  await expect
    .poll(async () => {
      const [node, canvas] = await Promise.all([
        page.getByTestId(testId).boundingBox(),
        page.getByTestId("canvas").boundingBox(),
      ]);
      if (node === null || canvas === null) return false;
      return (
        node.x >= canvas.x &&
        node.y >= canvas.y &&
        node.x + node.width <= canvas.x + canvas.width &&
        node.y + node.height <= canvas.y + canvas.height
      );
    })
    .toBe(true);
}

const EDITING_BASELINE = "e2e-baselines/editing";

test.beforeAll(async ({ request }) => {
  // serial suites are retried in a fresh worker, but the real web server and
  // workspace survive that retry. Restore immutable, out-of-catalog snapshots
  // before every worker so a failed attempt can never become the next
  // attempt's starting model (the 2026-07-15 Windows retry failure).
  for (const identity of ["flows/main", "flows/smoke", "flows/hint"]) {
    const name = identity.slice("flows/".length);
    const baseline = await request.get(
      `/api/flows/${EDITING_BASELINE}/${name}`,
    );
    expect(baseline.ok()).toBeTruthy();
    const { flow } = await baseline.json();
    const restored = await request.put(`/api/flows/${identity}`, {
      data: { flow, force: true },
    });
    expect(restored.ok()).toBeTruthy();
  }

  const codeBaseline = await request.get(
    `/api/code/${EDITING_BASELINE}/smoke`,
  );
  expect(codeBaseline.ok()).toBeTruthy();
  const { code } = await codeBaseline.json();
  const codeRestored = await request.put("/api/code/flows/smoke", {
    data: { code, force: true },
  });
  expect(codeRestored.ok()).toBeTruthy();
});

test.describe.configure({ mode: "serial" }); // shared workspace state

test("dragging a node autosaves a layout-only change", async ({ page }) => {
  await page.goto("/flow/flows/main");
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

  // One drag release records the entire gesture as one document step.
  await page.getByTestId("undo-canvas").click();
  await waitSaved(page);
  const restored = await flowModel(page, "flows/main");
  expect(restored.flow.layout).toEqual(before.flow.layout);
  await expect(page.getByTestId("undo-canvas")).toBeDisabled();
});

test("adding a node from the palette persists it", async ({ page }) => {
  await page.goto("/flow/flows/main");
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
  await page.goto("/flow/flows/main");
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
  await page.goto("/flow/flows/main");
  await page.getByTestId("node-log").click();
  await page.getByTestId("config-label").fill("hello from e2e");
  await page.getByTestId("config-label").blur();
  await waitSaved(page);

  const model = await flowModel(page, "flows/main");
  const log = model.flow.nodes.find((n: { id: string }) => n.id === "log");
  expect(log.config).toEqual({ label: "hello from e2e" });
});

test("canvas undo/redo is grouped, autosaved, focus-scoped, and run-locked", async ({
  page,
}) => {
  await page.goto("/flow/flows/main");
  await expect(page.getByTestId("undo-canvas")).toBeDisabled();
  await expect(page.getByTestId("redo-canvas")).toBeDisabled();

  const before = await flowModel(page, "flows/main");
  const originalLabel = before.flow.nodes.find(
    (node: { id: string }) => node.id === "log",
  ).config.label;

  await page.getByTestId("node-log").click();
  const label = page.getByTestId("config-label");
  await label.fill("");
  await label.pressSequentially("grouped history");
  await label.blur();
  await waitSaved(page);
  await expect(page.getByTestId("undo-canvas")).toBeEnabled();

  // A focused config input owns the shortcut: the canvas handler must leave
  // the browser event unclaimed and the existing canvas step untouched.
  await label.focus();
  await label.press("End");
  await label.pressSequentially("!");
  await expect(label).toHaveValue("grouped history!");
  await page.evaluate(() => {
    const target = window as Window & {
      __canvasUndoPrevented?: boolean;
    };
    window.addEventListener(
      "keydown",
      (event) => {
        queueMicrotask(() => {
          target.__canvasUndoPrevented = event.defaultPrevented;
        });
      },
      { once: true },
    );
  });
  await page.keyboard.press("ControlOrMeta+z");
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (window as Window & { __canvasUndoPrevented?: boolean })
            .__canvasUndoPrevented,
      ),
    )
    .toBe(false);
  // Controlled inputs do not expose a native undo stack consistently across
  // browsers. Return to the group baseline if the browser left the edit in
  // place; this must collapse the no-op history group either way.
  if ((await label.inputValue()) === "grouped history!") {
    await label.press("Backspace");
  }
  await expect(label).toHaveValue("grouped history");
  await label.blur();
  await waitSaved(page);
  await expect(page.getByTestId("undo-canvas")).toBeEnabled();
  let saved = await flowModel(page, "flows/main");
  expect(
    saved.flow.nodes.find((node: { id: string }) => node.id === "log").config
      .label,
  ).toBe("grouped history");

  // Every keystroke above is one focus/typing group.
  await page.locator(".react-flow__pane").click({ position: { x: 8, y: 8 } });
  await page.keyboard.press("ControlOrMeta+z");
  await waitSaved(page);
  saved = await flowModel(page, "flows/main");
  expect(
    saved.flow.nodes.find((node: { id: string }) => node.id === "log").config
      .label,
  ).toBe(originalLabel);

  const redoShortcut = await page.evaluate(() =>
    navigator.platform.startsWith("Mac") ? "Meta+Shift+z" : "Control+y",
  );
  await page.keyboard.press(redoShortcut);
  await waitSaved(page);
  saved = await flowModel(page, "flows/main");
  expect(
    saved.flow.nodes.find((node: { id: string }) => node.id === "log").config
      .label,
  ).toBe("grouped history");

  // CodeMirror retains its own native undo after a real edit; opening it
  // cannot consume canvas history even when the editor has focus.
  await page.getByTestId("open-code").click();
  const code = page.getByTestId("code-text").locator(".cm-content");
  await expect(code).toBeVisible();
  const codeBefore = await code.textContent();
  await code.click();
  await page.keyboard.press("ControlOrMeta+End");
  await page.keyboard.insertText("\n# native editor undo");
  await expect(code).toContainText("# native editor undo");
  await page.keyboard.press("ControlOrMeta+z");
  await expect
    .poll(() => code.textContent())
    .toBe(codeBefore);
  await page.getByTestId("code-close").click();
  await expect(page.getByTestId("undo-canvas")).toBeEnabled();

  await page.getByTestId("undo-canvas").click();
  await waitSaved(page);

  // History remains available while D29 run mode hides and guards editing.
  // Remove the intentionally unwired log so this editing fixture passes the
  // run gate; both edits are restored after leaving run mode.
  await page.getByTestId("node-log").click();
  await page.getByTestId("delete-node").click();
  await expect(page.getByTestId("node-log")).toHaveCount(0);
  await page.getByTestId("add-node").click();
  await page.getByTestId("palette-note").click();
  await expect(page.getByTestId("node-note")).toBeVisible();
  await page.getByTestId("run-button").click();
  await expect(page.getByTestId("run-pill-edit")).toBeVisible();
  await expect(page.getByTestId("undo-canvas")).toHaveCount(0);
  await expect(page.getByTestId("redo-canvas")).toHaveCount(0);
  await page.keyboard.press("ControlOrMeta+z");
  await expect(page.getByTestId("node-note")).toBeVisible();

  await page.getByTestId("run-pill-edit").click();
  await expect(page.getByTestId("undo-canvas")).toBeEnabled();
  await page.getByTestId("undo-canvas").click();
  await expect(page.getByTestId("node-note")).toHaveCount(0);
  await page.getByTestId("undo-canvas").click();
  await expect(page.getByTestId("node-log")).toBeVisible();
  await waitSaved(page);
});

test("multi-node deletion is one canvas history step", async ({ page }) => {
  await page.goto("/flow/flows/main");
  await expect(page.getByTestId("node-start")).toBeVisible();
  await expect(page.getByTestId("node-end")).toBeVisible();

  const wrappers = page.locator(".react-flow__node");
  const nodeCount = await wrappers.count();
  const boxes = await Promise.all(
    Array.from({ length: nodeCount }, (_, index) =>
      wrappers.nth(index).boundingBox(),
    ),
  );
  const present = boxes.filter((box) => box !== null);
  const left = Math.min(...present.map((box) => box.x)) - 8;
  const top = Math.min(...present.map((box) => box.y)) - 8;
  const right = Math.max(...present.map((box) => box.x + box.width)) + 8;
  const bottom = Math.max(...present.map((box) => box.y + box.height)) + 8;

  // Shift-drag is React Flow's platform-neutral multi-selection gesture.
  await page.keyboard.down("Shift");
  await page.waitForTimeout(50);
  await page.mouse.move(left, top);
  await page.mouse.down();
  await page.mouse.move(right, bottom, { steps: 5 });
  await page.mouse.up();
  await page.keyboard.up("Shift");
  await expect(page.locator(".react-flow__node.selected")).toHaveCount(
    nodeCount,
  );
  await page.keyboard.press("Delete");
  await expect(page.getByTestId("node-start")).toHaveCount(0);
  await expect(page.getByTestId("node-end")).toHaveCount(0);

  await page.getByTestId("undo-canvas").click();
  await expect(page.getByTestId("node-start")).toBeVisible();
  await expect(page.getByTestId("node-end")).toBeVisible();
  await waitSaved(page);
});

test("safety and typed request fields preserve native values and templates", async ({
  page,
}) => {
  await page.goto("/flow/flows/main");
  await page.getByTestId("add-node").click();
  await page.getByTestId("palette-request").click();
  await page.getByTestId("node-request").click();

  await page.getByTestId("node-max-seconds").fill("2.5");
  await page.getByTestId("node-max-seconds").blur();
  await page.getByTestId("config-timeout_s").fill("{{ env.REQUEST_TIMEOUT }}");
  await page.getByTestId("config-verify_tls").fill("{{ env.VERIFY_TLS }}");
  await waitSaved(page);

  let model = await flowModel(page, "flows/main");
  let request = model.flow.nodes.find(
    (node: { id: string }) => node.id === "request",
  );
  expect(request).toMatchObject({
    max_seconds: 2.5,
    config: {
      url: "",
      timeout_s: "{{ env.REQUEST_TIMEOUT }}",
      verify_tls: "{{ env.VERIFY_TLS }}",
    },
  });

  await page.getByTestId("config-timeout_s").fill("12.5");
  await page.getByTestId("config-verify_tls").fill("false");
  await waitSaved(page);
  model = await flowModel(page, "flows/main");
  request = model.flow.nodes.find(
    (node: { id: string }) => node.id === "request",
  );
  expect(request.config.timeout_s).toBe(12.5);
  expect(request.config.verify_tls).toBe(false);

  await page.getByTestId("delete-node").click();
  await waitSaved(page);
});

test("connecting onto a wired input replaces the edge (E004)", async ({
  page,
}) => {
  await page.goto("/flow/flows/main");
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
  await page.goto("/flow/flows/main");
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
  await page.goto("/flow/flows/smoke");
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
  await page.goto("/flow/flows/main");
  await page.getByTestId("add-node").click();
  await page.getByTestId("palette-switch").click();
  await expect(page.getByTestId("node-switch")).toBeVisible();
  await expectNodeInsideCanvas(page, "node-switch");
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
  await page.goto("/flow/flows/main");

  await page.getByTestId("node-start").click();
  await page.getByTestId("start-port-add").click();
  await page.getByTestId("start-port-name-0").fill("greeting");
  await page.getByTestId("start-port-type-0").selectOption("string");
  await waitSaved(page);

  // A native empty string is distinct from an absent default (required).
  await page.getByTestId("start-port-default-enabled-0").check();
  await waitSaved(page);
  const withEmptyDefault = await flowModel(page, "flows/main");
  const startWithEmpty = withEmptyDefault.flow.nodes.find(
    (n: { type: string }) => n.type === "start",
  );
  expect(startWithEmpty.config.ports).toEqual([
    { name: "greeting", type: "string", default: "" },
  ]);
  await page.getByTestId("start-port-default-enabled-0").uncheck();
  await waitSaved(page);

  // Adding a port changes the Start node's measured width. Re-fit before
  // selecting the other side of the graph; React Flow virtualizes nodes that
  // move outside the current viewport after a dimension change.
  await page.getByRole("button", { name: "Fit View" }).click();
  await expectNodeInsideCanvas(page, "node-end");
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
  await page.goto("/flow/flows/main");
  await page.getByTestId("node-start").click();

  // the previous test left port 0 (greeting: string); add a number port
  await page.getByTestId("start-port-add").click();
  await page.getByTestId("start-port-name-1").fill("retries");
  await page.getByTestId("start-port-type-1").selectOption("number");
  const cell = page.getByTestId("start-port-default-1");

  // a non-numeric default stays local (red border), never saves
  await cell.fill("not-a-number");
  await cell.blur();
  await expect(cell).toHaveCSS("border-color", "rgb(217, 112, 112)");

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

  // The same typed cell preserves template source for BIND-time rendering;
  // the engine applies number coercion after evaluating it (D25).
  await cell.fill("{{ env.RETRIES }}");
  await cell.blur();
  await waitSaved(page);
  const templated = await flowModel(page, "flows/main");
  const templatedStart = templated.flow.nodes.find(
    (n: { type: string }) => n.type === "start",
  );
  expect(templatedStart.config.ports[1].default).toBe("{{ env.RETRIES }}");

  // cleanup for the serial suite
  await page.getByTestId("start-port-remove-1").click();
  await waitSaved(page);
});

test("nodes.py editor round-trips code and reports syntax errors", async ({
  page,
}) => {
  // this test MUTATES flows/smoke's nodes.py — capture the scaffolded
  // original and restore it at the end (the "fix it back" retype below
  // is NOT the original summarize; leaving it breaks anyone who RUNS
  // smoke later — the 2026-07-08 CI red)
  const originalCode = (
    (await (await page.request.get("/api/code/flows/smoke")).json()) as {
      code: string;
    }
  ).code;

  await page.goto("/flow/flows/smoke");
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

  // restore the scaffolded original (last-write-wins force)
  const restored = await page.request.put("/api/code/flows/smoke", {
    data: { code: originalCode, force: true },
  });
  expect(restored.ok()).toBeTruthy();
});

test("dragging a mismatched wire shows the live W102 hint (never blocks)", async ({
  page,
}) => {
  // flows/hint has a subflow whose `count` input is number-typed;
  // start.out is object — hovering it mid-drag must raise the hint
  await page.goto("/flow/flows/hint");
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
  await page.goto("/flow/flows/main");
  await waitSaved(page);
  await dragNodeBy(page, "node-start", 25, 15);
  await waitSaved(page);
  await expect(page.getByTestId("undo-canvas")).toBeEnabled();
  const before = await flowModel(page, "flows/main");

  // simulate a git checkout / hand edit: PUT with force from outside
  const moved = JSON.parse(JSON.stringify(before.flow));
  moved.layout[Object.keys(moved.layout)[0]] = [640, 480];
  const external = await page.request.put("/api/flows/flows/main", {
    data: { flow: moved, force: true },
  });
  expect(external.ok()).toBeTruthy();

  // the etag poll picks it up and reloads WITHOUT a conflict prompt — the
  // canvas was clean, so it's a silent live-reload
  await expect(page.getByTestId("undo-canvas")).toBeDisabled({
    timeout: 10_000,
  });
  await expect(page.getByTestId("save-conflict")).toHaveCount(0);
  await waitSaved(page);
  await expect(page.getByTestId("redo-canvas")).toBeDisabled();
});

async function dragNodeBy(page: Page, testId: string, dx: number, dy: number) {
  const node = page.getByTestId(testId);
  const box = (await node.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(
    box.x + box.width / 2 + dx,
    box.y + box.height / 2 + dy,
    { steps: 3 },
  );
  await page.mouse.up();
  // React Flow moves the DOM synchronously with a successful gesture. Prove
  // the edit was accepted before a test attributes a later failure to save
  // coordination (and stay inside the one-second debounce window).
  await expect
    .poll(
      async () => {
        const moved = await node.boundingBox();
        return (
          moved !== null &&
          (Math.abs(moved.x - box.x) > 1 || Math.abs(moved.y - box.y) > 1)
        );
      },
      { timeout: 500 },
    )
    .toBe(true);
}

test("immediate flow navigation flushes the pending canvas edit", async ({
  page,
}) => {
  await page.goto("/flow/flows/main");
  const before = await flowModel(page, "flows/main");
  await dragNodeBy(page, "node-start", 70, 35);
  await expect(page.getByTestId("save-status")).toHaveAttribute(
    "data-state",
    "dirty",
  );

  // Click before the one-second debounce expires. openFlow must drain the
  // coordinator before it replaces the current detail.
  await pickFlow(page, "flows/smoke");
  await expect(page).toHaveURL(/\/flow\/flows\/smoke$/);
  await expect(page.getByTestId("undo-canvas")).toBeDisabled();
  await expect(page.getByTestId("redo-canvas")).toBeDisabled();
  const after = await flowModel(page, "flows/main");
  expect(after.flow.layout.start).not.toEqual(before.flow.layout.start);
});

test("an edit during an in-flight save queues one serialized successor", async ({
  page,
}) => {
  await page.goto("/flow/flows/main");
  const bodies: Array<{ flow: { layout: { start: [number, number] } } }> = [];
  let firstSeen!: () => void;
  const sawFirst = new Promise<void>((resolve) => {
    firstSeen = resolve;
  });
  let releaseFirst!: () => void;
  const firstGate = new Promise<void>((resolve) => {
    releaseFirst = resolve;
  });

  await page.route("**/api/flows/flows/main", async (route) => {
    if (route.request().method() !== "PUT") {
      await route.continue();
      return;
    }
    bodies.push(route.request().postDataJSON());
    if (bodies.length === 1) {
      firstSeen();
      await firstGate;
    }
    await route.continue();
  });

  await dragNodeBy(page, "node-start", 45, 20);
  await sawFirst;
  await dragNodeBy(page, "node-start", 55, 25);
  releaseFirst();
  await waitSaved(page);

  expect(bodies).toHaveLength(2);
  const final = await flowModel(page, "flows/main");
  expect(final.flow.layout.start).toEqual(bodies[1].flow.layout.start);
});

test("closing nodes.py immediately flushes before unmount", async ({ page }) => {
  const original = (
    (await (await page.request.get("/api/code/flows/smoke")).json()) as {
      code: string;
    }
  ).code;
  await page.goto("/flow/flows/smoke");
  await page.getByTestId("open-code").click();
  const content = page.getByTestId("code-text").locator(".cm-content");
  await content.click();
  await page.keyboard.press("ControlOrMeta+a");
  await page.keyboard.insertText(`${original}\n# close-flush\n`);
  await page.getByTestId("code-close").click();
  await expect(page.getByTestId("code-editor")).toHaveCount(0);

  const saved = await (await page.request.get("/api/code/flows/smoke")).json();
  expect(saved.code).toContain("# close-flush");
  const restored = await page.request.put("/api/code/flows/smoke", {
    data: { code: original, force: true },
  });
  expect(restored.ok()).toBeTruthy();
});

test("beforeunload prompts while an accepted edit is pending", async ({ page }) => {
  await page.goto("/flow/flows/main");
  await dragNodeBy(page, "node-start", 30, 15);

  const dialogPromise = page.waitForEvent("dialog");
  void page.evaluate(() => window.location.reload()).catch(() => null);
  const dialog = await dialogPromise;
  expect(dialog.type()).toBe("beforeunload");
  await dialog.dismiss();
  await waitSaved(page);
});

test("an ETag conflict blocks navigation until the user resolves it", async ({
  page,
}) => {
  await page.goto("/flow/flows/main");
  const before = await flowModel(page, "flows/main");
  await dragNodeBy(page, "node-start", 35, 20);

  // Land an external revision before the local one-second debounce. The
  // navigation flush must see 409 and leave both the canvas and URL in place.
  const externalFlow = structuredClone(before.flow);
  externalFlow.layout.end = [
    externalFlow.layout.end[0] + 1,
    externalFlow.layout.end[1],
  ];
  const external = await page.request.put("/api/flows/flows/main", {
    data: {
      flow: externalFlow,
      base_etag: before.etag,
    },
  });
  expect(external.ok()).toBeTruthy();

  await pickFlow(page, "flows/smoke");
  await expect(page).toHaveURL(/\/flow\/flows\/main$/);
  await expect(page.getByTestId("save-conflict")).toBeVisible();

  await page.getByTestId("conflict-overwrite").click();
  await waitSaved(page);
  const saved = await flowModel(page, "flows/main");
  expect(saved.flow.layout.start).not.toEqual(before.flow.layout.start);
  await pickFlow(page, "flows/smoke");
  await expect(page).toHaveURL(/\/flow\/flows\/smoke$/);
});
