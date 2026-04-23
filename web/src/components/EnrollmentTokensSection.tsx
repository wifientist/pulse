import { Trash2 } from "lucide-react";
import { useState } from "react";

import { revokeEnrollmentToken } from "../api/endpoints";
import { useSnapshotStore } from "../store/snapshot";
import { formatRelativeFromMs } from "../utils/time";

export default function EnrollmentTokensSection() {
  const snapshot = useSnapshotStore((s) => s.snapshot);
  const tokens = snapshot?.enrollment_tokens ?? [];
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (tokens.length === 0) return null;

  const onRevoke = async (id: number) => {
    if (!window.confirm("Revoke this enrollment token? New devices using it will fail to enroll.")) {
      return;
    }
    setError(null);
    setBusyId(id);
    try {
      await revokeEnrollmentToken(id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "revoke failed");
    } finally {
      setBusyId(null);
    }
  };

  const activeCount = tokens.filter((t) => !t.revoked).length;

  return (
    <section className="bg-white rounded-lg border border-slate-200 overflow-hidden">
      <header className="px-4 py-2 border-b border-slate-200 flex items-center justify-between">
        <h2 className="text-sm font-medium text-slate-900">Enrollment tokens</h2>
        <span className="text-xs text-slate-500">
          {activeCount} active / {tokens.length} total
        </span>
      </header>
      {error ? (
        <div className="px-4 py-2 bg-rose-50 text-rose-700 text-sm">{error}</div>
      ) : null}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-xs text-slate-500 uppercase bg-slate-50">
            <tr>
              <th className="px-4 py-2 text-left font-medium">Label</th>
              <th className="px-4 py-2 text-left font-medium">Created</th>
              <th className="px-4 py-2 text-left font-medium">Expires</th>
              <th className="px-4 py-2 text-left font-medium">Uses left</th>
              <th className="px-4 py-2 text-left font-medium">State</th>
              <th className="px-4 py-2" />
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {tokens.map((t) => {
              const expired =
                t.expires_at !== null && t.expires_at <= Date.now();
              const exhausted =
                t.uses_remaining !== null && t.uses_remaining <= 0;
              const inactive = t.revoked || expired || exhausted;
              return (
                <tr key={t.id}>
                  <td className="px-4 py-2 font-medium text-slate-900">
                    {t.label}
                  </td>
                  <td className="px-4 py-2 text-slate-600">
                    {formatRelativeFromMs(t.created_at)}
                  </td>
                  <td className="px-4 py-2 text-slate-600">
                    {t.expires_at === null
                      ? "never"
                      : formatRelativeFromMs(t.expires_at)}
                  </td>
                  <td className="px-4 py-2 text-slate-600">
                    {t.uses_remaining === null ? "∞" : t.uses_remaining}
                  </td>
                  <td className="px-4 py-2">
                    {t.revoked ? (
                      <span className="px-2 py-0.5 rounded text-xs font-medium bg-slate-100 text-slate-500">
                        revoked
                      </span>
                    ) : expired ? (
                      <span className="px-2 py-0.5 rounded text-xs font-medium bg-slate-100 text-slate-500">
                        expired
                      </span>
                    ) : exhausted ? (
                      <span className="px-2 py-0.5 rounded text-xs font-medium bg-slate-100 text-slate-500">
                        used up
                      </span>
                    ) : (
                      <span className="px-2 py-0.5 rounded text-xs font-medium bg-emerald-50 text-emerald-700">
                        active
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-right">
                    {!inactive ? (
                      <button
                        onClick={() => onRevoke(t.id)}
                        disabled={busyId === t.id}
                        className="inline-flex items-center gap-1 px-2 py-1 rounded text-xs text-rose-700 hover:bg-rose-50 disabled:opacity-50"
                      >
                        <Trash2 className="w-3 h-3" /> revoke
                      </button>
                    ) : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
