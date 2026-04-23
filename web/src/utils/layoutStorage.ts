// Persists user-customised mesh layout (node positions, per-edge handle choices,
// locked flag) to localStorage so they survive page reloads. Keyed by agent_uid /
// edge id — entries for items that no longer exist are left in storage but ignored.

const POSITIONS_KEY = "pulse_mesh_positions_v1";
const EDGE_HANDLES_KEY = "pulse_mesh_edge_handles_v1";
const LOCKED_KEY = "pulse_mesh_locked_v1";

export interface Position {
  x: number;
  y: number;
}

export interface EdgeHandles {
  source_handle: string;
  target_handle: string;
}

export function loadPositions(): Record<string, Position> {
  if (typeof localStorage === "undefined") return {};
  try {
    const raw = localStorage.getItem(POSITIONS_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, Position>;
    }
    return {};
  } catch {
    return {};
  }
}

export function savePositions(
  entries: Array<{ id: string; position: Position }>,
): void {
  if (typeof localStorage === "undefined") return;
  const existing = loadPositions();
  for (const { id, position } of entries) existing[id] = position;
  try {
    localStorage.setItem(POSITIONS_KEY, JSON.stringify(existing));
  } catch {
    /* quota / disabled — degrade silently */
  }
}

export function clearPositions(): void {
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.removeItem(POSITIONS_KEY);
  } catch {
    /* ignore */
  }
}

export function loadEdgeHandles(): Record<string, EdgeHandles> {
  if (typeof localStorage === "undefined") return {};
  try {
    const raw = localStorage.getItem(EDGE_HANDLES_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, EdgeHandles>;
    }
    return {};
  } catch {
    return {};
  }
}

export function saveEdgeHandles(
  entries: Array<{ id: string; handles: EdgeHandles | null }>,
): void {
  if (typeof localStorage === "undefined") return;
  const existing = loadEdgeHandles();
  for (const { id, handles } of entries) {
    if (handles) existing[id] = handles;
    else delete existing[id];
  }
  try {
    localStorage.setItem(EDGE_HANDLES_KEY, JSON.stringify(existing));
  } catch {
    /* ignore */
  }
}

export function clearEdgeHandles(): void {
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.removeItem(EDGE_HANDLES_KEY);
  } catch {
    /* ignore */
  }
}

export function loadLocked(): boolean {
  if (typeof localStorage === "undefined") return false;
  return localStorage.getItem(LOCKED_KEY) === "1";
}

export function saveLocked(locked: boolean): void {
  if (typeof localStorage === "undefined") return;
  try {
    if (locked) localStorage.setItem(LOCKED_KEY, "1");
    else localStorage.removeItem(LOCKED_KEY);
  } catch {
    /* ignore */
  }
}
