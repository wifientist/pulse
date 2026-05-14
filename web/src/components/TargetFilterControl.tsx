import { Eye, EyeOff, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { useSnapshotStore } from "../store/snapshot";
import { useTargetFilterStore } from "../store/targetFilter";

/**
 * Global per-target show/hide filter for passive ping targets. Sits in the
 * header next to the agent filter. Checked = visible (default), unchecked =
 * hidden. Hidden when no passive targets are configured so the header stays
 * tidy for setups that don't use the feature.
 */
export default function TargetFilterControl() {
  const snapshot = useSnapshotStore((s) => s.snapshot);
  const hidden = useTargetFilterStore((s) => s.hidden);
  const toggle = useTargetFilterStore((s) => s.toggle);
  const clear = useTargetFilterStore((s) => s.clear);

  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const wrapRef = useRef<HTMLDivElement | null>(null);

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

  const targets = useMemo(() => {
    const list = (snapshot?.passive_targets ?? []).filter((t) => t.enabled);
    return list.slice().sort((a, b) => a.name.localeCompare(b.name));
  }, [snapshot?.passive_targets]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return targets;
    return targets.filter(
      (t) =>
        t.name.toLowerCase().includes(needle)
        || t.ip.toLowerCase().includes(needle),
    );
  }, [targets, q]);

  if (targets.length === 0) return null;

  const hiddenCount = hidden.length;
  const label = hiddenCount === 0 ? "All targets" : `${hiddenCount} hidden`;
  const tone =
    hiddenCount === 0
      ? "text-slate-500 hover:text-slate-900"
      : "text-amber-700 bg-amber-50 hover:bg-amber-100";
  const Icon = hiddenCount === 0 ? Eye : EyeOff;

  return (
    <div ref={wrapRef} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className={`inline-flex items-center gap-1 text-sm px-2 py-1 rounded ${tone}`}
        title="Show or hide individual ping targets"
      >
        <Icon className="w-4 h-4" />
        <span>{label}</span>
      </button>
      {open ? (
        <div className="absolute right-0 mt-1 w-80 bg-white border border-slate-200 rounded-lg shadow-lg z-30 overflow-hidden">
          <div className="p-2 border-b border-slate-100 flex items-center gap-2">
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search targets"
              className="flex-1 text-sm px-2 py-1 border border-slate-200 rounded"
            />
            {hiddenCount > 0 ? (
              <button
                onClick={() => {
                  clear();
                  setQ("");
                }}
                className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-900"
                title="Show all targets"
              >
                <X className="w-3 h-3" /> show all
              </button>
            ) : null}
          </div>
          <div className="max-h-72 overflow-y-auto">
            {filtered.length === 0 ? (
              <div className="px-3 py-4 text-sm text-slate-400 text-center">
                no matches
              </div>
            ) : (
              filtered.map((t) => {
                const visible = !hidden.includes(t.id);
                return (
                  <label
                    key={t.id}
                    className="flex items-center gap-2 px-3 py-1.5 text-sm cursor-pointer hover:bg-slate-50"
                  >
                    <input
                      type="checkbox"
                      checked={visible}
                      onChange={() => toggle(t.id)}
                    />
                    <span className="font-medium text-slate-900">{t.name}</span>
                    <span className="text-xs text-slate-400 font-mono">
                      {t.ip}
                    </span>
                  </label>
                );
              })
            )}
          </div>
          <div className="px-3 py-2 border-t border-slate-100 text-xs text-slate-500">
            {hiddenCount === 0
              ? "All targets visible"
              : `${hiddenCount} of ${targets.length} hidden`}
          </div>
        </div>
      ) : null}
    </div>
  );
}
