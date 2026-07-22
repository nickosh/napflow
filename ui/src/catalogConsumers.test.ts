import { ReactFlowProvider } from "@xyflow/react";
import { createElement, type ComponentType } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";

import { NODE_META } from "./catalog";
import FlowNode from "./components/FlowNode";
import ConfigForm from "./components/ConfigForm";
import { CONFIG_FORMS, type FieldDescriptor } from "./forms";
import type { CanvasNode } from "./graph";
import { useAppStore } from "./store";

const originalRequestMeta = NODE_META.request;
const originalRequestForm = CONFIG_FORMS.request;

afterEach(() => {
  NODE_META.request = originalRequestMeta;
  CONFIG_FORMS.request = originalRequestForm;
  useAppStore.setState({
    runView: null,
    runFramePath: [],
    runFrameView: null,
    selectedNode: null,
  });
});

function renderRequestCard(): string {
  const data: CanvasNode["data"] = {
    nodeId: "req",
    nodeType: "request",
    config: { probe: "registry value" },
    inputs: [],
    outputs: [],
    errors: 0,
    warnings: 0,
    autoStart: false,
    ghostSource: false,
    ghostTarget: false,
  };
  return renderToStaticMarkup(
    createElement(
      ReactFlowProvider,
      null,
      createElement(FlowNode, { data, selected: false } as never),
    ),
  );
}

describe("catalog-backed production consumers", () => {
  it("renders a card's icon, width, and quick fields from NODE_META and CONFIG_FORMS", () => {
    const ProbeIcon: ComponentType<{ size?: number }> = ({ size }) =>
      createElement("svg", {
        "data-testid": "registry-probe-icon",
        "data-size": size,
      });
    const probeField: FieldDescriptor = {
      key: "probe",
      label: "registry quick field",
      kind: "string",
    };
    NODE_META.request = {
      ...originalRequestMeta,
      icon: ProbeIcon as typeof originalRequestMeta.icon,
      quick: ["probe"],
      width: 417,
    };
    CONFIG_FORMS.request = [probeField];

    const card = renderRequestCard();

    expect(card).toContain('data-testid="registry-probe-icon"');
    expect(card).toContain('data-size="15"');
    expect(card).toContain("width:417px");
    expect(card).toContain('data-testid="config-probe"');
    expect(card).toContain('placeholder="registry quick field"');
    expect(card).not.toContain('data-testid="config-method"');
  });

  it("renders labels, help, control kinds, and options from CONFIG_FORMS", () => {
    CONFIG_FORMS.request = [
      {
        key: "probe_text",
        label: "registry text label",
        kind: "string",
        placeholder: "registry text placeholder",
        help: "registry text help",
      },
      {
        key: "probe_choice",
        label: "registry choice label",
        kind: "select",
        options: ["alpha", "beta"],
      },
    ];

    const form = renderToStaticMarkup(
      createElement(ConfigForm, {
        nodeId: "req",
        nodeType: "request",
        config: { probe_choice: "beta" },
      }),
    );

    expect(form).toContain("registry text label");
    expect(form).toContain("registry text help");
    expect(form).toContain('data-testid="config-probe_text"');
    expect(form).toContain('placeholder="registry text placeholder"');
    expect(form).toContain('data-testid="config-probe_choice"');
    expect(form).toContain('<option value="alpha">alpha</option>');
    expect(form).toContain('<option value="beta" selected="">beta</option>');
  });
});
