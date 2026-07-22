// D11 soft port types: color feedback, never blocking. One palette,
// used by handles and edge strokes alike. Base tones for resting
// ports/wires, bright twins for run-mode "carried data" states
// (F1 Nocturne handoff palette).

export const PORT_TYPE_COLORS: Record<string, string> = {
  string: "#d9b45c",
  number: "#5cc4a8",
  boolean: "#a986e0",
  object: "#6ea8dc",
  list: "#5cb8c4",
  any: "#8a92a6",
};

export const PORT_TYPE_BRIGHT: Record<string, string> = {
  string: "#ffd166",
  number: "#3ddbb4",
  boolean: "#c39bff",
  object: "#59c2ff",
  list: "#4dd7e8",
  any: "#c9d2e4",
};

export function portColor(type: string | undefined): string {
  return PORT_TYPE_COLORS[type ?? "any"] ?? PORT_TYPE_COLORS.any;
}

export function portBright(type: string | undefined): string {
  return PORT_TYPE_BRIGHT[type ?? "any"] ?? PORT_TYPE_BRIGHT.any;
}
