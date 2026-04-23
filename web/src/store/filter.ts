// Global agent filter. Empty set = no filter (show all). Size 1 = focus / "1:n"
// mode (the selected agent plus its direct peers). Size >= 2 = strict subset.
// Consumed by every component that renders agent data (mesh diagram, status tiles,
// alerts feed, agents table) so toggling the filter updates everything at once.

import { create } from "zustand";

const STORAGE_KEY = "pulse_agent_filter_v1";

function loadInitial(): string[] {
  if (typeof localStorage === "undefined") return [];
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (Array.isArray(parsed) && parsed.every((x) => typeof x === "string")) {
      return parsed;
    }
  } catch {
    // ignore
  }
  return [];
}

function persist(uids: string[]): void {
  if (typeof localStorage === "undefined") return;
  try {
    if (uids.length === 0) localStorage.removeItem(STORAGE_KEY);
    else localStorage.setItem(STORAGE_KEY, JSON.stringify(uids));
  } catch {
    // quota / disabled — silent
  }
}

export type FilterMode = "all" | "focus" | "subset";

interface FilterStore {
  selected: string[];
  mode: FilterMode;
  setSelected: (uids: string[]) => void;
  toggle: (uid: string) => void;
  clear: () => void;
}

function modeFor(selected: string[]): FilterMode {
  if (selected.length === 0) return "all";
  if (selected.length === 1) return "focus";
  return "subset";
}

export const useFilterStore = create<FilterStore>((set, get) => {
  const initial = loadInitial();
  return {
    selected: initial,
    mode: modeFor(initial),
    setSelected: (uids) => {
      const dedup = Array.from(new Set(uids));
      persist(dedup);
      set({ selected: dedup, mode: modeFor(dedup) });
    },
    toggle: (uid) => {
      const cur = get().selected;
      const next = cur.includes(uid)
        ? cur.filter((x) => x !== uid)
        : [...cur, uid];
      persist(next);
      set({ selected: next, mode: modeFor(next) });
    },
    clear: () => {
      persist([]);
      set({ selected: [], mode: "all" });
    },
  };
});
