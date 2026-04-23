// Shared rules for resolving "which agent uids are visible" and "which edges pass"
// given the global filter. Keeps mesh / tiles / alerts / table all consistent.

import type { PeerAssignmentView, SnapshotEvent } from "../api/types";
import type { FilterMode } from "../store/filter";

export interface FilterContext {
  mode: FilterMode;
  selected: Set<string>;
  // uids visible on-screen given the current mode + snapshot peer graph
  visibleUids: Set<string>;
}

export function buildFilterContext(
  snapshot: SnapshotEvent | null,
  mode: FilterMode,
  selected: string[],
): FilterContext {
  const sel = new Set(selected);
  if (mode === "all" || !snapshot) {
    return { mode, selected: sel, visibleUids: new Set() };
  }
  if (mode === "subset") {
    return { mode, selected: sel, visibleUids: sel };
  }
  // focus (size 1) — include the selected agent + anything it has an enabled edge to
  const visible = new Set(sel);
  const only = selected[0];
  for (const pa of snapshot.peer_assignments) {
    if (!pa.enabled) continue;
    if (pa.source_agent_uid === only) visible.add(pa.target_agent_uid);
    if (pa.target_agent_uid === only) visible.add(pa.source_agent_uid);
  }
  return { mode, selected: sel, visibleUids: visible };
}

export function isAgentVisible(ctx: FilterContext, uid: string): boolean {
  if (ctx.mode === "all") return true;
  // Passive target nodes aren't agents; they're always visible. Their edges
  // are filtered by source-agent visibility elsewhere.
  if (uid.startsWith("passive:")) return true;
  return ctx.visibleUids.has(uid);
}

// Edge visibility rules:
//   - all: every edge shows
//   - focus: only edges where selected agent is source or target
//   - subset: only edges where BOTH endpoints are in the subset
export function isEdgeVisible(
  ctx: FilterContext,
  srcUid: string,
  tgtUid: string,
): boolean {
  if (ctx.mode === "all") return true;
  // Passive edges: show whenever the source agent is visible. Passive targets
  // themselves aren't filterable.
  if (tgtUid.startsWith("passive:")) {
    return ctx.mode === "focus"
      ? ctx.selected.has(srcUid)
      : ctx.selected.has(srcUid);
  }
  if (ctx.mode === "focus") {
    const only = ctx.selected.values().next().value;
    return srcUid === only || tgtUid === only;
  }
  return ctx.selected.has(srcUid) && ctx.selected.has(tgtUid);
}

export function filterPeerAssignments(
  ctx: FilterContext,
  peers: PeerAssignmentView[],
): PeerAssignmentView[] {
  if (ctx.mode === "all") return peers;
  return peers.filter((p) =>
    isEdgeVisible(ctx, p.source_agent_uid, p.target_agent_uid),
  );
}
