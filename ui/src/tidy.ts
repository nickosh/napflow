// F1 "tidy layout": pure layered auto-layout. Columns follow message
// direction (BFS from the nodes nothing points at — cycles are legal,
// so visited-tracking, not topological order); rows keep each node's
// current vertical order so a tidy never shuffles siblings.

const COLUMN_GAP = 60;
const ROW_GAP = 48;
const MARGIN = 40;
const FALLBACK_W = 240;
const FALLBACK_H = 120;

export type TidyNode = {
  id: string;
  position: { x: number; y: number };
  measured?: { width?: number; height?: number };
};

export function tidyPositions(
  nodes: TidyNode[],
  edges: { source: string; target: string }[],
): Record<string, { x: number; y: number }> {
  const ids = new Set(nodes.map((n) => n.id));
  const real = edges.filter((e) => ids.has(e.source) && ids.has(e.target));
  const incoming = new Set(real.map((e) => e.target));
  const outgoing = new Map<string, string[]>();
  for (const edge of real) {
    outgoing.set(edge.source, [...(outgoing.get(edge.source) ?? []), edge.target]);
  }

  const depth = new Map<string, number>();
  let queue = nodes.filter((n) => !incoming.has(n.id)).map((n) => n.id);
  if (queue.length === 0 && nodes.length > 0) queue = [nodes[0].id]; // pure cycle
  for (const id of queue) depth.set(id, 0);
  while (queue.length > 0) {
    const id = queue.shift()!;
    for (const next of outgoing.get(id) ?? []) {
      if (!depth.has(next)) {
        depth.set(next, (depth.get(id) ?? 0) + 1);
        queue.push(next);
      }
    }
  }

  const columns = new Map<number, TidyNode[]>();
  for (const node of nodes) {
    const col = depth.get(node.id) ?? 0;
    columns.set(col, [...(columns.get(col) ?? []), node]);
  }

  // column x positions account for the widest card in each column
  const sortedCols = [...columns.keys()].sort((a, b) => a - b);
  const colX = new Map<number, number>();
  let x = MARGIN;
  for (const col of sortedCols) {
    colX.set(col, x);
    const width = Math.max(
      ...columns.get(col)!.map((n) => n.measured?.width ?? FALLBACK_W),
    );
    x += width + COLUMN_GAP;
  }

  const positions: Record<string, { x: number; y: number }> = {};
  for (const col of sortedCols) {
    const stack = [...columns.get(col)!].sort(
      (a, b) => a.position.y - b.position.y,
    );
    let y = MARGIN;
    for (const node of stack) {
      positions[node.id] = { x: colX.get(col)!, y };
      y += (node.measured?.height ?? FALLBACK_H) + ROW_GAP;
    }
  }
  return positions;
}
