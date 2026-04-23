import dagre from "@dagrejs/dagre";
import { Position, type Edge, type Node } from "@xyflow/react";

import type {
  AgentView,
  LinkState,
  LinkStateView,
  SnapshotEvent,
} from "../api/types";

// @xyflow/react v12 requires node/edge `data` to be assignable to
// Record<string, unknown>. Using `type` form + index signature keeps TS happy without
// losing type-safety where we read the known fields.
export type MeshNodeData = {
  agent: AgentView;
  [key: string]: unknown;
};

export type MeshEdgeData = {
  sourceUid: string;
  targetUid: string;
  targetIp: string;
  state: LinkState | string;
  loss_pct_1m: number | null;
  rtt_p95_1m: number | null;
  [key: string]: unknown;
};

export type MeshNodeType = Node<MeshNodeData, "meshNode">;
// Edges use React Flow's built-in default (smooth bezier) — no custom type string.
export type MeshEdgeType = Edge<MeshEdgeData>;

export interface MeshGraph {
  nodes: MeshNodeType[];
  edges: MeshEdgeType[];
}

// Default handle ids for an edge's two endpoints. MeshNode renders one handle per side
// (top/right/bottom/left) and ReactFlow runs in ConnectionMode.Loose so either end of
// an edge can attach to any side. Users drag to reroute; the choice persists per edge.
export const DEFAULT_SOURCE_HANDLE = "right";
export const DEFAULT_TARGET_HANDLE = "left";

function layout(nodes: MeshNodeType[], edges: Edge[]): MeshNodeType[] {
  const NODE_WIDTH = 200;
  const NODE_HEIGHT = 80;
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: "LR", nodesep: 60, ranksep: 140 });
  nodes.forEach((n) => g.setNode(n.id, { width: NODE_WIDTH, height: NODE_HEIGHT }));
  edges.forEach((e) => g.setEdge(e.source, e.target));
  dagre.layout(g);
  return nodes.map((n) => {
    const pos = g.node(n.id);
    return {
      ...n,
      position: { x: pos.x - NODE_WIDTH / 2, y: pos.y - NODE_HEIGHT / 2 },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
    };
  });
}

/**
 * Turn a SnapshotEvent into a mesh graph ready for React Flow. Rules:
 *   - Nodes: one per active agent (state !== 'revoked')
 *   - Edges: one per enabled peer_assignment whose source+target agents are both nodes
 *   - Each edge carries its matching LinkStateView (or 'unknown' if none found)
 *   - Default edge endpoints go right-s → left-t; caller can override with saved
 *     per-edge handle choices
 */
export function buildMeshGraph(
  snapshot: SnapshotEvent,
  savedHandles: Record<
    string,
    { source_handle: string; target_handle: string }
  > = {},
): MeshGraph {
  const agentsByUid = new Map<string, AgentView>();
  for (const a of snapshot.agents) {
    if (a.state === "revoked") continue;
    agentsByUid.set(a.agent_uid, a);
  }

  const linkMap = new Map<string, LinkStateView>();
  for (const l of snapshot.link_states) {
    linkMap.set(`${l.source_agent_uid}->${l.target_agent_uid}`, l);
  }

  const nodes: MeshNodeType[] = Array.from(agentsByUid.values()).map((a) => ({
    id: a.agent_uid,
    type: "meshNode",
    position: { x: 0, y: 0 }, // overwritten by dagre
    data: { agent: a },
  }));

  const edges: MeshEdgeType[] = [];
  for (const pa of snapshot.peer_assignments) {
    if (!pa.enabled) continue;
    if (!agentsByUid.has(pa.source_agent_uid)) continue;
    if (!agentsByUid.has(pa.target_agent_uid)) continue;
    const id = `${pa.source_agent_uid}->${pa.target_agent_uid}`;
    const link = linkMap.get(id);
    const saved = savedHandles[id];
    edges.push({
      id,
      source: pa.source_agent_uid,
      target: pa.target_agent_uid,
      sourceHandle: saved?.source_handle ?? DEFAULT_SOURCE_HANDLE,
      targetHandle: saved?.target_handle ?? DEFAULT_TARGET_HANDLE,
      data: {
        sourceUid: pa.source_agent_uid,
        targetUid: pa.target_agent_uid,
        targetIp: pa.target_ip,
        state: link?.state ?? "unknown",
        loss_pct_1m: link?.loss_pct_1m ?? null,
        rtt_p95_1m: link?.rtt_p95_1m ?? null,
      },
    });
  }

  return { nodes: layout(nodes, edges), edges };
}
