import { Filter, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { useFilterStore } from "../store/filter";
import { useSnapshotStore } from "../store/snapshot";

/**
 * Global agent filter control. Sits in the header so it applies to every page.
 * Empty selection = show all (default). 1 selected = 1:n focus mode. 2+ = strict
 * subset.
 */
export default function FilterControl() {
  const snapshot = useSnapshotStore((s) => s.snapshot);
  const selected = useFilterStore((s) => s.selected);
  const mode = useFilterStore((s) => s.mode);
  const setSelected = useFilterStore((s) => s.setSelected);
  const toggle = useFilterStore((s) => s.toggle);
  const clear = useFilterStore((s) => s.clear);

  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const wrapRef = useRef<HTMLDivElement | null>(null);

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  const agents = useMemo(() => {
    const list = (snapshot?.agents ?? []).filter((a) => a.state !== "revoked");
    return list.slice().sort((a, b) => a.hostname.localeCompare(b.hostname));
  }, [snapshot?.agents]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return agents;
    return agents.filter(
      (a) =>
        a.hostname.toLowerCase().includes(needle) ||
        a.agent_uid.toLowerCase().includes(needle),
    );
  }, [agents, q]);

  const label =
    mode === "all"
      ? "All agents"
      : mode === "focus"
        ? `Focus: ${hostnameFor(snapshot?.agents ?? [], selected[0])}`
        : `${selected.length} selected`;

  const tone =
    mode === "all"
      ? "text-slate-500 hover:text-slate-900"
      : "text-sky-700 bg-sky-50 hover:bg-sky-100";

  return (
    <div ref={wrapRef} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className={`inline-flex items-center gap-1 text-sm px-2 py-1 rounded ${tone}`}
        title="Global agent filter — applies to every page"
      >
        <Filter className="w-4 h-4" />
        <span>{label}</span>
      </button>
      {open ? (
        <div className="absolute right-0 mt-1 w-80 bg-white border border-slate-200 rounded-lg shadow-lg z-30 overflow-hidden">
          <div className="p-2 border-b border-slate-100 flex items-center gap-2">
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search hostnames"
              className="flex-1 text-sm px-2 py-1 border border-slate-200 rounded"
            />
            {selected.length > 0 ? (
              <button
                onClick={() => {
                  clear();
                  setQ("");
                }}
                className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-900"
                title="Clear filter"
              >
                <X className="w-3 h-3" /> clear
              </button>
            ) : null}
          </div>
          <div className="max-h-72 overflow-y-auto">
            {filtered.length === 0 ? (
              <div className="px-3 py-4 text-sm text-slate-400 text-center">
                no matches
              </div>
            ) : (
              filtered.map((a) => {
                const checked = selected.includes(a.agent_uid);
                return (
                  <label
                    key={a.agent_uid}
                    className="flex items-center gap-2 px-3 py-1.5 text-sm cursor-pointer hover:bg-slate-50"
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggle(a.agent_uid)}
                    />
                    <span className="font-medium text-slate-900">
                      {a.hostname}
                    </span>
                    <span className="text-xs text-slate-400 font-mono">
                      {a.primary_ip ?? "—"}
                    </span>
                  </label>
                );
              })
            )}
          </div>
          <div className="px-3 py-2 border-t border-slate-100 text-xs text-slate-500 flex items-center justify-between">
            <span>
              {mode === "all"
                ? "No filter (showing all)"
                : mode === "focus"
                  ? "Focus mode (1:n): selected agent + its peers"
                  : "Subset mode: selected agents + their internal edges"}
            </span>
            {selected.length > 0 ? (
              <button
                onClick={() => setSelected([])}
                className="text-sky-600 hover:text-sky-800"
              >
                reset
              </button>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function hostnameFor(
  agents: { agent_uid: string; hostname: string }[],
  uid: string | undefined,
): string {
  if (!uid) return "—";
  const a = agents.find((x) => x.agent_uid === uid);
  return a ? a.hostname : uid.slice(0, 8) + "…";
}
