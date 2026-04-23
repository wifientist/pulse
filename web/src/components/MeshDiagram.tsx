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
import { LayoutGrid, Lock, Unlock } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { useSnapshotStore } from "../store/snapshot";
import {
  DEFAULT_SOURCE_HANDLE,
  DEFAULT_TARGET_HANDLE,
  buildMeshGraph,
  type MeshEdgeData,
  type MeshEdgeType,
  type MeshNodeType,
} from "../utils/derive";
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
    return {
      ...e,
      animated: data?.state === "up",
      reconnectable: true,
      style: {
        stroke: style.stroke,
        strokeWidth: style.strokeWidth,
        strokeDasharray: style.strokeDasharray,
      },
      markerEnd: {
        type: MarkerType.ArrowClosed,
        width: 14,
        height: 14,
        color: style.stroke,
      },
      labelStyle: { fontSize: 10, fill: "#475569" },
      labelBgStyle: { fill: "white", fillOpacity: 0.9 },
      labelBgBorderRadius: 4,
      labelBgPadding: [4, 2] as [number, number],
      label: hasStats
        ? `${formatMs(rtt ?? null)} · ${formatPct(loss ?? null)}`
        : (data?.state ?? "unknown"),
    };
  });
}

export default function MeshDiagram() {
  const snapshot = useSnapshotStore((s) => s.snapshot);

  // Controlled mode: parent owns nodes/edges state and passes them to ReactFlow as
  // props. onNodesChange/onEdgesChange feed React Flow's internal interactions (drag,
  // reconnect, etc.) back into our state.
  const [nodes, setNodes, onNodesChange] = useNodesState<MeshNodeType>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<MeshEdgeType>([]);
  const [locked, setLocked] = useState<boolean>(() => loadLocked());
  const edgeReconnectSuccessful = useRef(true);

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

    setNodes((current) => {
      const currentByUid = new Map(current.map((n) => [n.id, n]));
      return g.nodes.map((fresh) => {
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
    setEdges(styleEdges(g.edges));
  }, [snapshot, setNodes, setEdges]);

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
    </div>
  );
}
