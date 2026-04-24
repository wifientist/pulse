import {
  Background,
  ConnectionMode,
  Controls,
  MarkerType,
  MiniMap,
  Panel,
  ReactFlow,
  reconnectEdge,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type NodeChange,
} from "@xyflow/react";
import { LayoutGrid, Lock, Unlock, Waypoints } from "lucide-react";
import React, { useCallback, useEffect, useRef, useState } from "react";

import { useFilterStore } from "../store/filter";
import { useSnapshotStore } from "../store/snapshot";
import { buildFilterContext, isAgentVisible, isEdgeVisible } from "../utils/filterView";
import {
  DEFAULT_SOURCE_HANDLE,
  DEFAULT_TARGET_HANDLE,
  buildMeshGraph,
  type MeshEdgeData,
  type MeshEdgeType,
  type MeshNodeType,
} from "../utils/derive";
import { autoEdgeHandles } from "../utils/autoEdge";
import { styleForState } from "../utils/edgeColor";
import {
  clearEdgeHandles,
  clearPositions,
  loadEdgeHandles,
  loadLocked,
  loadPositions,
  saveEdgeHandles,
  saveLocked,
  savePositions,
} from "../utils/layoutStorage";
import { formatMs, formatPct } from "../utils/time";

import MeshNode from "./MeshNode";

const nodeTypes = { meshNode: MeshNode };

/**
 * Apply visual styling to every edge based on its link state. Called each time edges
 * are derived from a fresh snapshot. Arrow marker is baked into every edge so
 * direction is always visible, regardless of which handles the user attached.
 */
function styleEdges(edges: MeshEdgeType[]): MeshEdgeType[] {
  return edges.map((e) => {
    const data = e.data as MeshEdgeData | undefined;
    const style = styleForState(data?.state);
    const loss = data?.loss_pct_1m;
    const rtt = data?.rtt_p95_1m;
    const hasStats = rtt != null || loss != null;
    const isBidi = !!data?.is_bidi;
    const marker = {
      type: MarkerType.ArrowClosed,
      width: 14,
      height: 14,
      color: style.stroke,
    };
    return {
      ...e,
      animated: data?.state === "up",
      reconnectable: true,
      style: {
        stroke: style.stroke,
        strokeWidth: style.strokeWidth,
        strokeDasharray: style.strokeDasharray,
      },
      markerEnd: marker,
      // Bidi edges get an arrow at both ends so the consolidated line
      // still reads as "both directions".
      markerStart: isBidi ? marker : undefined,
      labelStyle: { fontSize: 10, fill: "#475569" },
      labelBgStyle: { fill: "white", fillOpacity: 0.9 },
      labelBgBorderRadius: 4,
      labelBgPadding: [4, 2] as [number, number],
      label: hasStats
        ? `${isBidi ? "↔ " : ""}${formatMs(rtt ?? null)} · ${formatPct(loss ?? null)}`
        : (data?.state ?? "unknown"),
    };
  });
}

export default function MeshDiagram() {
  const snapshot = useSnapshotStore((s) => s.snapshot);
  const filterMode = useFilterStore((s) => s.mode);
  const filterSelected = useFilterStore((s) => s.selected);

  // Controlled mode: parent owns nodes/edges state and passes them to ReactFlow as
  // props. onNodesChange/onEdgesChange feed React Flow's internal interactions (drag,
  // reconnect, etc.) back into our state.
  const [nodes, setNodes, onNodesChange] = useNodesState<MeshNodeType>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<MeshEdgeType>([]);
  const [locked, setLocked] = useState<boolean>(() => loadLocked());
  const edgeReconnectSuccessful = useRef(true);

  // Floating tooltip for edges — shown on hover. For bidi agent pairs we
  // render both directions' stats on separate lines so the consolidated
  // line is still self-documenting.
  const [hoverEdge, setHoverEdge] = useState<{
    edge: MeshEdgeType;
    x: number;
    y: number;
  } | null>(null);
  const onEdgeMouseEnter = useCallback(
    (event: React.MouseEvent, edge: Edge) => {
      setHoverEdge({
        edge: edge as MeshEdgeType,
        x: event.clientX,
        y: event.clientY,
      });
    },
    [],
  );
  const onEdgeMouseMove = useCallback(
    (event: React.MouseEvent, edge: Edge) => {
      setHoverEdge({
        edge: edge as MeshEdgeType,
        x: event.clientX,
        y: event.clientY,
      });
    },
    [],
  );
  const onEdgeMouseLeave = useCallback(() => setHoverEdge(null), []);

  /**
   * Reconcile our state from each snapshot:
   *   - Node ids that already exist keep their current position (user drags stick).
   *   - New node ids use localStorage-saved position if present, else dagre.
   *   - Removed node ids drop out.
   *   - Edges are replaced wholesale each tick, but per-edge handle choices come from
   *     localStorage so reconnections stick across reloads.
   */
  useEffect(() => {
    if (!snapshot) {
      setNodes([]);
      setEdges([]);
      return;
    }
    const savedPositions = loadPositions();
    const savedHandles = loadEdgeHandles();
    const g = buildMeshGraph(snapshot, savedHandles);

    // Apply the global filter — hide nodes/edges that the current mode excludes.
    // Done AFTER buildMeshGraph so positions from dagre still consider the full
    // topology (prevents visible nodes from shifting when the filter changes).
    const ctx = buildFilterContext(snapshot, filterMode, filterSelected);
    const visibleNodes = g.nodes.filter((n) => isAgentVisible(ctx, n.id));
    const visibleEdges = g.edges.filter((e) =>
      isEdgeVisible(ctx, e.source, e.target),
    );

    setNodes((current) => {
      const currentByUid = new Map(current.map((n) => [n.id, n]));
      return visibleNodes.map((fresh) => {
        const existing = currentByUid.get(fresh.id);
        if (existing) {
          return { ...fresh, position: existing.position };
        }
        if (savedPositions[fresh.id]) {
          return { ...fresh, position: savedPositions[fresh.id] };
        }
        return fresh;
      });
    });
    setEdges(styleEdges(visibleEdges));
  }, [snapshot, setNodes, setEdges, filterMode, filterSelected]);

  // Drag end → persist every node's position so reloads keep the layout.
  const onNodeDragStop = useCallback(() => {
    setNodes((current) => {
      savePositions(current.map((n) => ({ id: n.id, position: n.position })));
      return current;
    });
  }, [setNodes]);

  // --- Edge reconnection -------------------------------------------------
  // React Flow v12 pattern: on reconnect start we optimistically mark failure, swap
  // to success in onReconnect, and if the user drops off-handle we can remove or
  // revert in onReconnectEnd. We revert (because our edges are server-owned).
  const onReconnectStart = useCallback(() => {
    edgeReconnectSuccessful.current = false;
  }, []);

  const onReconnect = useCallback(
    (oldEdge: Edge, newConnection: Connection) => {
      edgeReconnectSuccessful.current = true;
      setEdges((eds) => {
        // shouldReplaceId:false — keep the server-derived edge id (`${srcUid}->${tgtUid}`)
        // so our savedHandles lookup and snapshot reconciliation stay aligned.
        const updated = reconnectEdge(oldEdge, newConnection, eds, {
          shouldReplaceId: false,
        }) as MeshEdgeType[];
        // Persist the handle choice for this edge id.
        const swapped = updated.find((e) => e.id === oldEdge.id);
        if (swapped) {
          saveEdgeHandles([
            {
              id: swapped.id,
              handles: {
                source_handle:
                  swapped.sourceHandle ?? DEFAULT_SOURCE_HANDLE,
                target_handle:
                  swapped.targetHandle ?? DEFAULT_TARGET_HANDLE,
              },
            },
          ]);
        }
        return updated;
      });
    },
    [setEdges],
  );

  const onReconnectEnd = useCallback(() => {
    // If the user dropped off a handle, snap the edge back to whatever it had before —
    // our edges belong to the server, not user-deletable.
    if (!edgeReconnectSuccessful.current) {
      // reconnectEdge already handled the in-hand edge; nothing to do.
    }
    edgeReconnectSuccessful.current = true;
  }, []);

  // --- Toolbar buttons ---------------------------------------------------
  const autoArrange = useCallback(() => {
    if (!snapshot) return;
    clearPositions();
    clearEdgeHandles();
    const g = buildMeshGraph(snapshot);
    setNodes(g.nodes);
    setEdges(styleEdges(g.edges));
    savePositions(g.nodes.map((n) => ({ id: n.id, position: n.position })));
  }, [snapshot, setNodes, setEdges]);

  // Re-route edges based on current node positions. Nodes stay put; for each edge
  // we pick the handle on each node whose side faces the other endpoint. Persisted
  // via saveEdgeHandles so the choice survives reloads and snapshot ticks.
  const autoEdge = useCallback(() => {
    const picks = autoEdgeHandles(nodes, edges);
    saveEdgeHandles(picks);
    const pickById = new Map(picks.map((p) => [p.id, p.handles]));
    setEdges(
      edges.map((e) => {
        const h = pickById.get(e.id);
        if (!h) return e;
        return {
          ...e,
          sourceHandle: h.source_handle,
          targetHandle: h.target_handle,
        };
      }),
    );
  }, [nodes, edges, setEdges]);

  const toggleLock = useCallback(() => {
    setLocked((prev) => {
      const next = !prev;
      saveLocked(next);
      return next;
    });
  }, []);

  // Wrap onNodesChange to suppress position changes while locked.
  const onNodesChangeGated = useCallback(
    (changes: NodeChange<MeshNodeType>[]) => {
      if (locked) {
        const filtered = changes.filter((c) => c.type !== "position");
        if (filtered.length === 0) return;
        onNodesChange(filtered);
        return;
      }
      onNodesChange(changes);
    },
    [locked, onNodesChange],
  );

  if (!snapshot) {
    return (
      <div className="bg-white rounded-lg border border-slate-200 h-[60vh] animate-pulse" />
    );
  }

  const showEmptyOverlay = nodes.length === 0;

  return (
    <div className="bg-white rounded-lg border border-slate-200 h-[60vh] relative">
      {showEmptyOverlay ? (
        <div className="absolute inset-0 grid place-items-center text-slate-500 text-sm pointer-events-none z-10">
          No active agents yet. Approve a pending enrollment to see the mesh.
        </div>
      ) : null}
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChangeGated}
        onEdgesChange={onEdgesChange}
        onNodeDragStop={onNodeDragStop}
        onReconnect={onReconnect}
        onReconnectStart={onReconnectStart}
        onReconnectEnd={onReconnectEnd}
        onEdgeMouseEnter={onEdgeMouseEnter}
        onEdgeMouseMove={onEdgeMouseMove}
        onEdgeMouseLeave={onEdgeMouseLeave}
        nodeTypes={nodeTypes}
        nodesDraggable={!locked}
        nodesConnectable={false}
        edgesFocusable
        connectionMode={ConnectionMode.Loose}
        reconnectRadius={16}
        fitView
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={20} color="#e2e8f0" />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable />
        <Panel position="top-right">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={autoArrange}
              title="Re-run automatic layout, reset handle choices"
              className="inline-flex items-center gap-1 bg-white border border-slate-200 shadow-sm rounded px-2 py-1 text-xs text-slate-700 hover:bg-slate-50"
            >
              <LayoutGrid className="w-3 h-3" /> Auto arrange
            </button>
            <button
              type="button"
              onClick={autoEdge}
              title="Keep node positions, pick edge endpoints that face the other node"
              className="inline-flex items-center gap-1 bg-white border border-slate-200 shadow-sm rounded px-2 py-1 text-xs text-slate-700 hover:bg-slate-50"
            >
              <Waypoints className="w-3 h-3" /> Auto edge
            </button>
            <button
              type="button"
              onClick={toggleLock}
              title={
                locked ? "Unlock layout (allow dragging)" : "Lock current layout"
              }
              className={
                locked
                  ? "inline-flex items-center gap-1 bg-slate-900 border border-slate-900 shadow-sm rounded px-2 py-1 text-xs text-white hover:bg-slate-800"
                  : "inline-flex items-center gap-1 bg-white border border-slate-200 shadow-sm rounded px-2 py-1 text-xs text-slate-700 hover:bg-slate-50"
              }
            >
              {locked ? (
                <>
                  <Lock className="w-3 h-3" /> Locked
                </>
              ) : (
                <>
                  <Unlock className="w-3 h-3" /> Unlocked
                </>
              )}
            </button>
          </div>
        </Panel>
      </ReactFlow>
      {hoverEdge ? <EdgeTooltip hover={hoverEdge} snapshot={snapshot} /> : null}
    </div>
  );
}


/** Floating hover tooltip for mesh edges. Shows per-direction stats for
 * bidi agent pairs (two lines) or a single line for unidirectional / passive
 * edges. Positioned at the cursor; pointer-events-none so it never steals
 * hover events. */
function EdgeTooltip({
  hover,
  snapshot,
}: {
  hover: { edge: MeshEdgeType; x: number; y: number };
  snapshot: ReturnType<typeof useSnapshotStore.getState>["snapshot"];
}) {
  const data = hover.edge.data as MeshEdgeData | undefined;
  if (!data) return null;
  const hostnameOf = (uid: string) => {
    if (uid.startsWith("passive:")) {
      const id = Number(uid.slice("passive:".length));
      return snapshot?.passive_targets.find((p) => p.id === id)?.name ?? uid;
    }
    return (
      snapshot?.agents.find((a) => a.agent_uid === uid)?.hostname
        ?? uid.slice(0, 8)
    );
  };
  const DirRow = ({
    from,
    to,
    state,
    rtt,
    loss,
  }: {
    from: string;
    to: string;
    state: string;
    rtt: number | null;
    loss: number | null;
  }) => (
    <div className="flex items-center gap-1.5 leading-tight">
      <span className="text-slate-500 text-xs">{hostnameOf(from)}</span>
      <span className="text-slate-400">→</span>
      <span className="text-slate-500 text-xs">{hostnameOf(to)}</span>
      <StatePill state={state} />
      <span className="font-mono text-xs text-slate-700 ml-auto">
        {formatMs(rtt)} · {formatPct(loss)}
      </span>
    </div>
  );
  return (
    <div
      className="fixed z-50 pointer-events-none bg-white border border-slate-200 rounded shadow-lg px-3 py-2 text-xs min-w-[280px]"
      style={{ left: hover.x + 12, top: hover.y + 12 }}
    >
      {data.forward ? (
        <DirRow
          from={data.forward.sourceUid}
          to={data.forward.targetUid}
          state={String(data.forward.state)}
          rtt={data.forward.rtt_p95_1m}
          loss={data.forward.loss_pct_1m}
        />
      ) : null}
      {data.reverse ? (
        <DirRow
          from={data.reverse.sourceUid}
          to={data.reverse.targetUid}
          state={String(data.reverse.state)}
          rtt={data.reverse.rtt_p95_1m}
          loss={data.reverse.loss_pct_1m}
        />
      ) : null}
      {/* Passive edges don't have forward/reverse populated — fall back to
          the aggregate so the tooltip still says something useful. */}
      {!data.forward && !data.reverse ? (
        <DirRow
          from={data.sourceUid}
          to={data.targetUid}
          state={String(data.state)}
          rtt={data.rtt_p95_1m}
          loss={data.loss_pct_1m}
        />
      ) : null}
    </div>
  );
}


function StatePill({ state }: { state: string }) {
  const cls =
    state === "up"
      ? "bg-emerald-50 text-emerald-700"
      : state === "degraded"
        ? "bg-amber-50 text-amber-700"
        : state === "down"
          ? "bg-rose-50 text-rose-700"
          : "bg-slate-100 text-slate-500";
  return (
    <span className={`px-1 py-0 rounded text-[10px] font-semibold ${cls}`}>
      {state}
    </span>
  );
}
