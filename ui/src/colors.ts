// D11 soft port types: color feedback, never blocking. One palette,
// used by handles and edge strokes alike.

export const PORT_TYPE_COLORS: Record<string, string> = {
  string: "#2e7d32",
  number: "#1565c0",
  boolean: "#7b1fa2",
  object: "#ef6c00",
  list: "#00838f",
  any: "#757575",
};

export function portColor(type: string | undefined): string {
  return PORT_TYPE_COLORS[type ?? "any"] ?? PORT_TYPE_COLORS.any;
}
