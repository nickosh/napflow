import { beforeEach, describe, expect, it } from "vitest";

import { useChrome } from "../uiChrome";
import { collectRunInputs, type StartPort } from "./RunControls";

beforeEach(() => {
  useChrome.getState().closeRunPopover();
});

describe("run input default/override distinction", () => {
  const ports: StartPort[] = [
    { name: "text", type: "string", default: "configured" },
    { name: "count", type: "number", default: "{{ env.COUNT }}" },
    { name: "required", type: "string" },
  ];

  it("omits untouched defaults so the engine evaluates them at BIND", () => {
    expect(
      collectRunInputs(
        ports,
        { text: "configured", count: "{{ env.COUNT }}", required: "" },
        new Set(),
      ),
    ).toEqual({ inputs: {}, invalid: new Set() });
  });

  it("preserves an edited empty string instead of silently reusing default", () => {
    expect(
      collectRunInputs(
        ports,
        { text: "", count: "{{ env.COUNT }}", required: "" },
        new Set(["text"]),
      ),
    ).toEqual({ inputs: { text: "" }, invalid: new Set() });
  });

  it("sends an edited value even when it matches the configured default", () => {
    expect(
      collectRunInputs(
        ports,
        { text: "configured", count: "{{ env.COUNT }}", required: "" },
        new Set(["text"]),
      ),
    ).toEqual({ inputs: { text: "configured" }, invalid: new Set() });
  });

  it("accepts edited blanks only for string and any inputs", () => {
    const blankPorts: StartPort[] = [
      { name: "string", type: "string", default: "configured" },
      { name: "any", type: "any", default: 1 },
      { name: "number", type: "number", default: 1 },
      { name: "boolean", type: "boolean", default: true },
      { name: "object", type: "object", default: { configured: true } },
      { name: "list", type: "list", default: [1] },
    ];
    expect(
      collectRunInputs(
        blankPorts,
        Object.fromEntries(blankPorts.map((port) => [port.name, ""])),
        new Set(blankPorts.map((port) => port.name)),
      ),
    ).toEqual({
      inputs: { string: "", any: "" },
      invalid: new Set(["number", "boolean", "object", "list"]),
    });
  });
});

describe("shared run popover owner", () => {
  const everyType: StartPort[] = [
    { name: "any", type: "any", default: { configured: true } },
    { name: "string", type: "string", default: "configured" },
    { name: "number", type: "number", default: 2 },
    { name: "boolean", type: "boolean", default: false },
    { name: "object", type: "object", default: { nested: 1 } },
    { name: "list", type: "list", default: [1, 2] },
    { name: "template", type: "number", default: "{{ env.COUNT }}" },
    { name: "required", type: "string" },
  ];

  it("initializes every type identically and leaves defaults for BIND", () => {
    useChrome.getState().openRunPopover("flows/one", everyType);
    const state = useChrome.getState();

    expect(state.runPopoverFlow).toBe("flows/one");
    expect(state.runInputPorts).toEqual(everyType);
    expect(state.runInputCells).toEqual({
      any: '{"configured":true}',
      string: "configured",
      number: "2",
      boolean: "false",
      object: '{"nested":1}',
      list: "[1,2]",
      template: "{{ env.COUNT }}",
      required: "",
    });
    expect(
      collectRunInputs(
        state.runInputPorts,
        state.runInputCells,
        state.runInputEdited,
      ),
    ).toEqual({ inputs: {}, invalid: new Set() });
  });

  it("resets cells, edits, and validation on close and reopen", () => {
    const chrome = useChrome.getState();
    chrome.openRunPopover("flows/one", everyType);
    useChrome.getState().editRunInput("number", "bad");
    useChrome.getState().setRunInputInvalid(new Set(["number"]));
    useChrome.getState().closeRunPopover();

    expect(useChrome.getState()).toMatchObject({
      runPopoverOpen: false,
      runPopoverFlow: null,
      runInputPorts: [],
      runInputCells: {},
      runInputEdited: new Set(),
      runInputInvalid: new Set(),
    });

    useChrome.getState().openRunPopover("flows/one", [
      { name: "number", type: "number", default: 7 },
    ]);
    expect(useChrome.getState()).toMatchObject({
      runPopoverOpen: true,
      runPopoverFlow: "flows/one",
      runInputCells: { number: "7" },
      runInputEdited: new Set(),
      runInputInvalid: new Set(),
    });
  });

  it("discards an open model when flow navigation changes identity", () => {
    useChrome.getState().openRunPopover("flows/one", everyType);
    useChrome.getState().editRunInput("string", "stale");

    useChrome.getState().syncRunPopoverFlow("flows/two", everyType);

    expect(useChrome.getState()).toMatchObject({
      runPopoverOpen: false,
      runPopoverFlow: null,
      runInputPorts: [],
      runInputCells: {},
      runInputEdited: new Set(),
      runInputInvalid: new Set(),
    });
  });

  it("preserves edits when an equivalent Start schema is refreshed", () => {
    useChrome.getState().openRunPopover("flows/one", everyType);
    useChrome.getState().editRunInput("string", "draft");

    useChrome
      .getState()
      .syncRunPopoverFlow("flows/one", structuredClone(everyType));

    expect(useChrome.getState()).toMatchObject({
      runPopoverOpen: true,
      runPopoverFlow: "flows/one",
      runInputCells: expect.objectContaining({ string: "draft" }),
      runInputEdited: new Set(["string"]),
    });
  });

  it("discards edits when the current flow's Start schema changes", () => {
    useChrome.getState().openRunPopover("flows/one", everyType);
    useChrome.getState().editRunInput("string", "stale");

    useChrome.getState().syncRunPopoverFlow(
      "flows/one",
      everyType.map((port) =>
        port.name === "number" ? { ...port, default: 3 } : port,
      ),
    );

    expect(useChrome.getState()).toMatchObject({
      runPopoverOpen: false,
      runPopoverFlow: null,
      runInputPorts: [],
      runInputCells: {},
      runInputEdited: new Set(),
      runInputInvalid: new Set(),
    });
  });

  it("closes a stale model when the last Start port is deleted", () => {
    useChrome.getState().openRunPopover("flows/one", [
      { name: "only", type: "string", default: "configured" },
    ]);
    useChrome.getState().editRunInput("only", "stale");

    useChrome.getState().syncRunPopoverFlow("flows/one", []);

    expect(useChrome.getState()).toMatchObject({
      runPopoverOpen: false,
      runPopoverFlow: null,
      runInputPorts: [],
      runInputCells: {},
      runInputEdited: new Set(),
      runInputInvalid: new Set(),
    });
  });

  it("Escape's compatibility close resets the model before reopen", () => {
    useChrome.getState().openRunPopover("flows/one", everyType);
    useChrome.getState().editRunInput("number", "bad");
    useChrome.getState().setRunInputInvalid(new Set(["number"]));

    useChrome.getState().setRunPopoverOpen(false);
    useChrome.getState().openRunPopover("flows/one", everyType);

    expect(useChrome.getState()).toMatchObject({
      runPopoverOpen: true,
      runPopoverFlow: "flows/one",
      runInputCells: expect.objectContaining({ number: "2" }),
      runInputEdited: new Set(),
      runInputInvalid: new Set(),
    });
  });
});
