import { Pencil, Plus, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import {
  createPassiveTarget,
  deletePassiveTarget,
  updatePassiveTarget,
} from "../api/endpoints";
import type { PassiveTargetView } from "../api/types";
import { useSnapshotStore } from "../store/snapshot";
import { formatMs, formatPct } from "../utils/time";

// Short-circuit aggregate of the per-(agent, target) passive_link_states: the
// *worst* state observed across all agents becomes the headline. Keeps the
// row compact while still signalling trouble.
const STATE_RANK: Record<string, number> = {
  down: 3,
  degraded: 2,
  unknown: 1,
  up: 0,
};

const STATE_BADGE: Record<string, string> = {
  up: "bg-emerald-50 text-emerald-700",
  degraded: "bg-amber-50 text-amber-700",
  down: "bg-rose-50 text-rose-700",
  unknown: "bg-slate-100 text-slate-500",
};

interface EditorState {
  id: number | null;
  name: string;
  ip: string;
  notes: string;
  enabled: boolean;
}

function emptyEditor(): EditorState {
  return { id: null, name: "", ip: "", notes: "", enabled: true };
}
function editorFromRow(r: PassiveTargetView): EditorState {
  return {
    id: r.id,
    name: r.name,
    ip: r.ip,
    notes: r.notes ?? "",
    enabled: r.enabled,
  };
}

export default function PassiveTargetsSection() {
  const snapshot = useSnapshotStore((s) => s.snapshot);
  const rows = snapshot?.passive_targets ?? [];
  const linkStates = snapshot?.passive_link_states ?? [];

  const sorted = useMemo(
    () =>
      [...rows].sort((a, b) => {
        if (a.enabled !== b.enabled) return a.enabled ? -1 : 1;
        return a.name.localeCompare(b.name);
      }),
    [rows],
  );

  // For each target id, aggregate the link states across agents.
  const statusByTarget = useMemo(() => {
    const out = new Map<
      number,
      {
        worst: string;
        best_loss_pct: number | null;
        worst_rtt_p95: number | null;
        agents_total: number;
        agents_up: number;
      }
    >();
    for (const r of rows) {
      out.set(r.id, {
        worst: "unknown",
        best_loss_pct: null,
        worst_rtt_p95: null,
        agents_total: 0,
        agents_up: 0,
      });
    }
    for (const ls of linkStates) {
      const bucket = out.get(ls.passive_target_id);
      if (!bucket) continue;
      bucket.agents_total += 1;
      if (ls.state === "up") bucket.agents_up += 1;
      if ((STATE_RANK[ls.state] ?? 0) > (STATE_RANK[bucket.worst] ?? 0)) {
        bucket.worst = ls.state;
      }
      if (ls.loss_pct_1m != null) {
        if (
          bucket.best_loss_pct == null
          || ls.loss_pct_1m < bucket.best_loss_pct
        ) {
          bucket.best_loss_pct = ls.loss_pct_1m;
        }
      }
      if (ls.rtt_p95_1m != null) {
        if (
          bucket.worst_rtt_p95 == null
          || ls.rtt_p95_1m > bucket.worst_rtt_p95
        ) {
          bucket.worst_rtt_p95 = ls.rtt_p95_1m;
        }
      }
    }
    return out;
  }, [rows, linkStates]);

  const [editor, setEditor] = useState<EditorState | null>(null);
  const [busyId, setBusyId] = useState<number | "new" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const openNew = () => {
    setError(null);
    setEditor(emptyEditor());
  };
  const openEdit = (r: PassiveTargetView) => {
    setError(null);
    setEditor(editorFromRow(r));
  };
  const close = () => setEditor(null);

  const onSubmit = async () => {
    if (!editor) return;
    const name = editor.name.trim();
    const ip = editor.ip.trim();
    if (!name) return setError("name required");
    if (!ip) return setError("ip required");
    setError(null);
    setBusyId(editor.id ?? "new");
    try {
      if (editor.id === null) {
        await createPassiveTarget({
          name,
          ip,
          notes: editor.notes.trim() || null,
          enabled: editor.enabled,
        });
      } else {
        await updatePassiveTarget(editor.id, {
          name,
          ip,
          notes: editor.notes.trim() || null,
          enabled: editor.enabled,
        });
      }
      setEditor(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "save failed");
    } finally {
      setBusyId(null);
    }
  };

  const onDelete = async (r: PassiveTargetView) => {
    if (!window.confirm(`Delete passive target "${r.name}" (${r.ip})?`)) return;
    setError(null);
    setBusyId(r.id);
    try {
      await deletePassiveTarget(r.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "delete failed");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="bg-white rounded-lg border border-slate-200 overflow-hidden">
      <header className="px-4 py-2 border-b border-slate-200 flex items-center justify-between">
        <div>
          <h2 className="text-sm font-medium text-slate-900">Passive targets</h2>
          <p className="text-xs text-slate-500">
            Ping-only endpoints (no agent). All active agents ping each enabled
            target; state below is the worst seen across any agent.
          </p>
        </div>
        <button
          onClick={openNew}
          className="inline-flex items-center gap-1 px-3 py-1.5 rounded bg-sky-500 text-white hover:bg-sky-600 text-sm"
        >
          <Plus className="w-4 h-4" /> Add target
        </button>
      </header>
      {error ? (
        <div className="px-4 py-2 bg-rose-50 text-rose-700 text-sm">{error}</div>
      ) : null}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-xs text-slate-500 uppercase bg-slate-50">
            <tr>
              <th className="px-4 py-2 text-left font-medium">Name</th>
              <th className="px-4 py-2 text-left font-medium">IP</th>
              <th className="px-4 py-2 text-left font-medium">State</th>
              <th className="px-4 py-2 text-right font-medium">Best loss</th>
              <th className="px-4 py-2 text-right font-medium">Worst p95</th>
              <th className="px-4 py-2 text-right font-medium">Agents up</th>
              <th className="px-4 py-2 text-left font-medium">Notes</th>
              <th className="px-4 py-2 text-right font-medium">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {sorted.map((r) => {
              const st = statusByTarget.get(r.id);
              const state = r.enabled ? (st?.worst ?? "unknown") : "disabled";
              const badge =
                state === "disabled"
                  ? "bg-slate-100 text-slate-500 line-through"
                  : (STATE_BADGE[state] ?? "bg-slate-100 text-slate-700");
              return (
                <tr key={r.id} className="hover:bg-slate-50">
                  <td className="px-4 py-2 font-medium text-slate-900">
                    {r.name}
                  </td>
                  <td className="px-4 py-2 font-mono text-slate-700">{r.ip}</td>
                  <td className="px-4 py-2">
                    <span
                      className={`px-2 py-0.5 rounded text-xs font-medium ${badge}`}
                    >
                      {state}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-slate-700">
                    {formatPct(st?.best_loss_pct ?? null)}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-slate-700">
                    {formatMs(st?.worst_rtt_p95 ?? null)}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-slate-500">
                    {st ? `${st.agents_up}/${st.agents_total}` : "—"}
                  </td>
                  <td className="px-4 py-2 text-xs text-slate-500 max-w-xs truncate">
                    {r.notes ?? "—"}
                  </td>
                  <td className="px-4 py-2 text-right">
                    <button
                      onClick={() => openEdit(r)}
                      disabled={busyId === r.id}
                      className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs text-slate-600 hover:bg-slate-100 disabled:opacity-50"
                    >
                      <Pencil className="w-3 h-3" /> edit
                    </button>
                    <button
                      onClick={() => onDelete(r)}
                      disabled={busyId === r.id}
                      className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs text-rose-600 hover:bg-rose-50 disabled:opacity-50 ml-2"
                    >
                      <Trash2 className="w-3 h-3" /> delete
                    </button>
                  </td>
                </tr>
              );
            })}
            {sorted.length === 0 ? (
              <tr>
                <td
                  colSpan={8}
                  className="px-4 py-6 text-center text-slate-500 text-sm"
                >
                  No passive targets yet.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      {editor ? (
        <div className="fixed inset-0 z-20 grid place-items-center bg-black/30">
          <div className="bg-white rounded-lg shadow-lg border border-slate-200 w-full max-w-lg p-5 space-y-4">
            <h2 className="text-sm font-semibold text-slate-900">
              {editor.id === null ? "Add passive target" : "Edit passive target"}
            </h2>
            {error ? (
              <div className="px-3 py-2 bg-rose-50 text-rose-700 text-sm rounded">
                {error}
              </div>
            ) : null}
            <div className="grid grid-cols-2 gap-3 text-sm">
              <label className="col-span-1">
                <div className="text-xs text-slate-500 mb-1">Name</div>
                <input
                  value={editor.name}
                  onChange={(e) =>
                    setEditor({ ...editor, name: e.target.value })
                  }
                  className="w-full border border-slate-200 rounded px-2 py-1"
                  placeholder="Gateway"
                />
              </label>
              <label className="col-span-1">
                <div className="text-xs text-slate-500 mb-1">IP</div>
                <input
                  value={editor.ip}
                  onChange={(e) => setEditor({ ...editor, ip: e.target.value })}
                  className="w-full border border-slate-200 rounded px-2 py-1 font-mono"
                  placeholder="10.0.0.1"
                />
              </label>
              <label className="col-span-2">
                <div className="text-xs text-slate-500 mb-1">Notes</div>
                <textarea
                  value={editor.notes}
                  onChange={(e) =>
                    setEditor({ ...editor, notes: e.target.value })
                  }
                  rows={3}
                  className="w-full border border-slate-200 rounded px-2 py-1"
                  placeholder="Optional — model, role, etc."
                />
              </label>
              <label className="col-span-2 inline-flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={editor.enabled}
                  onChange={(e) =>
                    setEditor({ ...editor, enabled: e.target.checked })
                  }
                />
                <span>Enabled (agents will ping this target)</span>
              </label>
            </div>
            <div className="flex justify-end gap-2">
              <button
                onClick={close}
                disabled={busyId !== null}
                className="px-3 py-1.5 rounded text-sm text-slate-600 hover:bg-slate-100 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={onSubmit}
                disabled={busyId !== null}
                className="px-3 py-1.5 rounded bg-sky-500 text-white hover:bg-sky-600 text-sm disabled:opacity-50"
              >
                {busyId !== null ? "Saving…" : "Save"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
