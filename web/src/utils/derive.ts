import dagre from "@dagrejs/dagre";
import { Position, type Edge, type Node } from "@xyflow/react";

import type {
  AgentView,
  LinkState,
  LinkStateView,
  PassiveTargetView,
  SnapshotEvent,
} from "../api/types";

// @xyflow/react v12 requires node/edge `data` to be assignable to
// Record<string, unknown>. Using `type` form + index signature keeps TS happy without
// losing type-safety where we read the known fields.
//
// A node in the mesh is either a pingable agent (with `agent` set) or a
// passive target (with `passive` set, `passive_state` rolled up across the
// agents currently pinging it). The single MeshNode component branches on
// which is present.
export type MeshNodeData = {
  agent?: AgentView;
  passive?: PassiveTargetView;
  passive_state?: LinkState | string;
  [key: string]: unknown;
};

export type MeshEdgeData = {
  sourceUid: string;
  targetUid: string;
  targetIp: string;
  state: LinkState | string;
  loss_pct_1m: number | null;
  rtt_p95_1m: number | null;
  is_passive?: boolean;
  [key: string]: unknown;
};

export type MeshNodeType = Node<MeshNodeData, "meshNode">;

// Node id prefix for passive target nodes. Mirrors the sentinel used on the
// wire for peer assignments so the mesh layer stays aware these aren't agents.
export const PASSIVE_NODE_PREFIX = "passive:";
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

  // Pre-compute which (src, tgt) pairs have a reverse edge too — used below to pick
  // bidi-safe defaults so two opposite edges don't perfectly overlap (and their
  // animated dashes alias into a flickering blink). One direction per pair keeps
  // the geometric default; the other gets a perpendicular routing.
  const enabledPairs = new Set(
    snapshot.peer_assignments
      .filter(
        (p) =>
          p.enabled
          && agentsByUid.has(p.source_agent_uid)
          && agentsByUid.has(p.target_agent_uid),
      )
      .map((p) => `${p.source_agent_uid}->${p.target_agent_uid}`),
  );

  const edges: MeshEdgeType[] = [];
  for (const pa of snapshot.peer_assignments) {
    if (!pa.enabled) continue;
    if (!agentsByUid.has(pa.source_agent_uid)) continue;
    if (!agentsByUid.has(pa.target_agent_uid)) continue;
    const id = `${pa.source_agent_uid}->${pa.target_agent_uid}`;
    const reverseId = `${pa.target_agent_uid}->${pa.source_agent_uid}`;
    const isBidi = enabledPairs.has(reverseId);
    // For bidi pairs, the direction whose source uid sorts later gets the
    // perpendicular routing — keeps the choice deterministic and stable between
    // snapshots.
    const isSecondary =
      isBidi && pa.source_agent_uid > pa.target_agent_uid;
    const defaultSource = isSecondary ? "top" : DEFAULT_SOURCE_HANDLE;
    const defaultTarget = isSecondary ? "top" : DEFAULT_TARGET_HANDLE;

    const link = linkMap.get(id);
    const saved = savedHandles[id];
    edges.push({
      id,
      source: pa.source_agent_uid,
      target: pa.target_agent_uid,
      sourceHandle: saved?.source_handle ?? defaultSource,
      targetHandle: saved?.target_handle ?? defaultTarget,
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

  // Passive targets: one node per enabled target, one edge per agent→target
  // pair whose link state we know (via passive_link_states). Aggregate the
  // worst per-agent state onto the node for a single node-level color.
  const passiveStateRank: Record<string, number> = {
    down: 3,
    degraded: 2,
    unknown: 1,
    up: 0,
  };
  const passiveLinkByKey = new Map<string, (typeof snapshot.passive_link_states)[number]>();
  for (const pls of snapshot.passive_link_states) {
    passiveLinkByKey.set(
      `${pls.source_agent_uid}->${PASSIVE_NODE_PREFIX}${pls.passive_target_id}`,
      pls,
    );
  }
  for (const pt of snapshot.passive_targets) {
    if (!pt.enabled) continue;
    const nodeId = `${PASSIVE_NODE_PREFIX}${pt.id}`;
    // Roll up per-agent state to node badge.
    let worst = "unknown";
    for (const pls of snapshot.passive_link_states) {
      if (pls.passive_target_id !== pt.id) continue;
      if (
        (passiveStateRank[pls.state] ?? 0)
        > (passiveStateRank[worst] ?? 0)
      ) {
        worst = pls.state;
      }
    }
    nodes.push({
      id: nodeId,
      type: "meshNode",
      position: { x: 0, y: 0 },
      data: { passive: pt, passive_state: worst },
    });
    // One directed edge per agent that could ping this target.
    for (const a of agentsByUid.values()) {
      if (a.state !== "active") continue;
      const edgeId = `${a.agent_uid}->${nodeId}`;
      const pls = passiveLinkByKey.get(edgeId);
      const saved2 = savedHandles[edgeId];
      edges.push({
        id: edgeId,
        source: a.agent_uid,
        target: nodeId,
        sourceHandle: saved2?.source_handle ?? DEFAULT_SOURCE_HANDLE,
        targetHandle: saved2?.target_handle ?? DEFAULT_TARGET_HANDLE,
        data: {
          sourceUid: a.agent_uid,
          targetUid: nodeId,
          targetIp: pt.ip,
          state: pls?.state ?? "unknown",
          loss_pct_1m: pls?.loss_pct_1m ?? null,
          rtt_p95_1m: pls?.rtt_p95_1m ?? null,
          is_passive: true,
        },
      });
    }
  }

  return { nodes: layout(nodes, edges), edges };
}
