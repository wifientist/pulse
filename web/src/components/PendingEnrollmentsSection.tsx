import { useState } from "react";
import { Check, X } from "lucide-react";

import { approveEnrollment, rejectEnrollment } from "../api/endpoints";
import { useSnapshotStore } from "../store/snapshot";
import { formatRelativeFromMs } from "../utils/time";

export default function PendingEnrollmentsSection() {
  const snapshot = useSnapshotStore((s) => s.snapshot);
  const pending = snapshot?.pending_enrollments ?? [];
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (pending.length === 0) return null;

  const onApprove = async (id: number) => {
    setError(null);
    setBusyId(id);
    try {
      await approveEnrollment(id, {});
    } catch (e) {
      setError(e instanceof Error ? e.message : "approve failed");
    } finally {
      setBusyId(null);
    }
  };

  const onReject = async (id: number) => {
    setError(null);
    setBusyId(id);
    try {
      await rejectEnrollment(id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "reject failed");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="bg-white rounded-lg border border-amber-200 border-l-4 border-l-amber-500">
      <header className="px-4 py-2 border-b border-amber-200 flex items-center justify-between">
        <h2 className="text-sm font-medium text-slate-900">
          Pending enrollments
        </h2>
        <span className="text-xs text-slate-500">{pending.length} waiting</span>
      </header>
      {error ? (
        <div className="px-4 py-2 bg-rose-50 text-rose-700 text-sm">{error}</div>
      ) : null}
      <ul className="divide-y divide-slate-100">
        {pending.map((p) => (
          <li key={p.id} className="px-4 py-3 flex items-center gap-4">
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium text-slate-900 truncate">
                {p.reported_hostname}
              </div>
              <div className="text-xs text-slate-500 font-mono truncate">
                {p.reported_ip} ·{" "}
                {typeof p.caps.os === "string" ? p.caps.os : "—"} · requested{" "}
                {formatRelativeFromMs(p.created_at)}
              </div>
            </div>
            <button
              onClick={() => onApprove(p.id)}
              disabled={busyId === p.id}
              className="inline-flex items-center gap-1 px-3 py-1.5 rounded bg-emerald-500 text-white hover:bg-emerald-600 disabled:opacity-50 text-sm"
            >
              <Check className="w-4 h-4" /> Approve
            </button>
            <button
              onClick={() => onReject(p.id)}
              disabled={busyId === p.id}
              className="inline-flex items-center gap-1 px-3 py-1.5 rounded bg-slate-100 text-slate-700 hover:bg-slate-200 disabled:opacity-50 text-sm"
            >
              <X className="w-4 h-4" /> Reject
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
