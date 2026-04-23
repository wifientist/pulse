// Given current node positions, pick the most visually sensible source/target handle
// for each edge — the side of each node that faces the other endpoint. Used by the
// "Auto-edge" toolbar button so the user can keep a custom node layout and get a
// one-click cleanup of edge routing without re-running dagre.

import type { MeshEdgeType, MeshNodeType } from "./derive";

// Must match what MeshNode renders. Four sides, one handle per side.
const SIDES = ["right", "bottom", "left", "top"] as const;
type Side = (typeof SIDES)[number];

// Node dimensions used for layout; kept here (not imported from derive.ts) because
// derive.ts treats them as private constants. If MeshNode's size changes notably we
// can bump these — a ~20% mismatch still produces sensible routing.
const NODE_W = 200;
const NODE_H = 80;

function sideFromAngle(angle: number): Side {
  // angle is atan2 result (-π..π) where +x is 0 and +y is downward (screen coords).
  // Thresholds split the plane into four ~90° sectors centered on each cardinal.
  const deg = ((angle * 180) / Math.PI + 360) % 360;
  if (deg >= 315 || deg < 45) return "right";
  if (deg < 135) return "bottom";
  if (deg < 225) return "left";
  return "top";
}

function opposite(side: Side): Side {
  return side === "left"
    ? "right"
    : side === "right"
      ? "left"
      : side === "top"
        ? "bottom"
        : "top";
}

export interface EdgeHandlePick {
  id: string;
  handles: { source_handle: string; target_handle: string };
}

// If primary routing uses a horizontal axis (left/right), the perpendicular side
// (top or bottom) avoids overlap for a bidi pair. Same idea swapped for a vertical
// primary. We always pick the `top`/`right` neighbor — picking consistently keeps
// the bidi reverse edge in a predictable place instead of flipping between ticks.
function perpendicularOf(side: Side): Side {
  return side === "right" || side === "left" ? "top" : "right";
}

/**
 * Compute a handle pick for every edge. For each edge, shoot a ray from the source
 * node's center to the target node's center: the source leaves through the side
 * that faces the target, and the target receives on its opposite side. Result is
 * what users typically sketch by hand (east-going edge → right→left, etc).
 *
 * Bidirectional pairs (both A→B and B→A enabled) would otherwise share the same
 * geometric axis and draw on top of each other. We detect them and route the
 * direction whose source uid sorts later on the perpendicular axis so both animated
 * paths are visible side-by-side.
 */
export function autoEdgeHandles(
  nodes: MeshNodeType[],
  edges: MeshEdgeType[],
): EdgeHandlePick[] {
  const posByUid = new Map(nodes.map((n) => [n.id, n.position]));
  const edgeIds = new Set(edges.map((e) => e.id));
  const out: EdgeHandlePick[] = [];
  for (const e of edges) {
    const s = posByUid.get(e.source);
    const t = posByUid.get(e.target);
    if (!s || !t) continue;
    const dx = t.x + NODE_W / 2 - (s.x + NODE_W / 2);
    const dy = t.y + NODE_H / 2 - (s.y + NODE_H / 2);

    let srcSide: Side;
    let tgtSide: Side;
    if (dx === 0 && dy === 0) {
      srcSide = "right";
      tgtSide = "left";
    } else {
      srcSide = sideFromAngle(Math.atan2(dy, dx));
      tgtSide = opposite(srcSide);
    }

    // Bidi? If so, let the lexicographically-later direction take the
    // perpendicular routing so both animated paths stay visible.
    const reverseId = `${e.target}->${e.source}`;
    const isBidi = edgeIds.has(reverseId);
    if (isBidi && e.source > e.target) {
      const perp = perpendicularOf(srcSide);
      srcSide = perp;
      tgtSide = perp;
    }

    out.push({
      id: e.id,
      handles: { source_handle: srcSide, target_handle: tgtSide },
    });
  }
  return out;
}
