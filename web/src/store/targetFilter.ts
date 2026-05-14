// Per-target show/hide filter for passive ping targets. Empty set = no
// targets hidden (default). Each entry is a passive_target_id; when present,
// that target's node and the edges leading to it drop out of the mesh.
// Mirrors store/filter.ts in shape so consumers can pick it up the same way.

import { create } from "zustand";

const STORAGE_KEY = "pulse_target_filter_v1";

function loadInitial(): number[] {
  if (typeof localStorage === "undefined") return [];
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (Array.isArray(parsed) && parsed.every((x) => typeof x === "number")) {
      return parsed;
    }
  } catch {
    // ignore
  }
  return [];
}

function persist(ids: number[]): void {
  if (typeof localStorage === "undefined") return;
  try {
    if (ids.length === 0) localStorage.removeItem(STORAGE_KEY);
    else localStorage.setItem(STORAGE_KEY, JSON.stringify(ids));
  } catch {
    // quota / disabled — silent
  }
}

interface TargetFilterStore {
  hidden: number[];
  setHidden: (ids: number[]) => void;
  toggle: (id: number) => void;
  clear: () => void;
}

export const useTargetFilterStore = create<TargetFilterStore>((set, get) => {
  const initial = loadInitial();
  return {
    hidden: initial,
    setHidden: (ids) => {
      const dedup = Array.from(new Set(ids));
      persist(dedup);
      set({ hidden: dedup });
    },
    toggle: (id) => {
      const cur = get().hidden;
      const next = cur.includes(id)
        ? cur.filter((x) => x !== id)
        : [...cur, id];
      persist(next);
      set({ hidden: next });
    },
    clear: () => {
      persist([]);
      set({ hidden: [] });
    },
  };
});
