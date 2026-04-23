import { useFilterStore } from "../store/filter";
import { useSnapshotStore } from "../store/snapshot";
import { stateBadgeClass } from "../utils/edgeColor";
import { buildFilterContext, isEdgeVisible } from "../utils/filterView";
import { formatRelativeFromMs } from "../utils/time";

export default function AlertsFeed({ limit = 20 }: { limit?: number }) {
  const snapshot = useSnapshotStore((s) => s.snapshot);
  const filterMode = useFilterStore((s) => s.mode);
  const filterSelected = useFilterStore((s) => s.selected);
  const ctx = buildFilterContext(snapshot, filterMode, filterSelected);
  const alerts = (snapshot?.recent_alerts ?? [])
    .filter((a) => isEdgeVisible(ctx, a.source_agent_uid, a.target_agent_uid))
    .slice(0, limit);
  const agentsByUid = new Map(
    (snapshot?.agents ?? []).map((a) => [a.agent_uid, a]),
  );

  const label = (uid: string) =>
    agentsByUid.get(uid)?.hostname ?? uid.slice(0, 8);

  return (
    <section className="bg-white rounded-lg border border-slate-200">
      <header className="px-4 py-2 border-b border-slate-200 flex items-center justify-between">
        <h2 className="text-sm font-medium text-slate-900">Recent alerts</h2>
        <span className="text-xs text-slate-500">last hour</span>
      </header>
      {alerts.length === 0 ? (
        <div className="px-4 py-6 text-sm text-slate-500 text-center">
          No alerts in the last hour.
        </div>
      ) : (
        <ul className="divide-y divide-slate-100">
          {alerts.map((a) => (
            <li key={a.id} className="px-4 py-2 text-sm flex items-center gap-3">
              <span className="text-xs text-slate-500 w-20 shrink-0">
                {formatRelativeFromMs(a.at_ts)}
              </span>
              <span className="font-mono text-xs text-slate-700">
                {label(a.source_agent_uid)} → {label(a.target_agent_uid)}
              </span>
              <span className="ml-auto flex items-center gap-1">
                <span className={stateBadgeClass(a.from_state)}>
                  {a.from_state}
                </span>
                <span className="text-slate-400">→</span>
                <span className={stateBadgeClass(a.to_state)}>{a.to_state}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
