import { create } from "zustand";

import type { StreamStatus } from "../api/sse";
import type { SnapshotEvent } from "../api/types";

interface SnapshotState {
  snapshot: SnapshotEvent | null;
  status: StreamStatus;
  lastEventAt: number | null;
  setSnapshot: (s: SnapshotEvent) => void;
  setStatus: (status: StreamStatus) => void;
  noteHeartbeat: (ts: number) => void;
  reset: () => void;
}

export const useSnapshotStore = create<SnapshotState>((set) => ({
  snapshot: null,
  status: "connecting",
  lastEventAt: null,
  setSnapshot: (s) =>
    set({ snapshot: s, lastEventAt: Date.now(), status: "live" }),
  setStatus: (status) => set({ status }),
  noteHeartbeat: (ts) => set({ lastEventAt: ts }),
  reset: () =>
    set({ snapshot: null, status: "connecting", lastEventAt: null }),
}));
