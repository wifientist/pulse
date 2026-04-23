import { AlertTriangle, CheckCircle2, Clock, Users } from "lucide-react";

import { useFilterStore } from "../store/filter";
import { useSnapshotStore } from "../store/snapshot";
import { buildFilterContext, isAgentVisible, isEdgeVisible } from "../utils/filterView";

interface TileProps {
  label: string;
  value: string | number;
  subtitle?: string;
  icon: React.ReactNode;
  tone?: "ok" | "warn" | "alert" | "neutral";
}

function Tile({ label, value, subtitle, icon, tone = "neutral" }: TileProps) {
  const toneClass =
    tone === "ok"
      ? "text-emerald-600"
      : tone === "warn"
        ? "text-amber-600"
        : tone === "alert"
          ? "text-rose-600"
          : "text-slate-600";

  return (
    <div className="bg-white rounded-lg border border-slate-200 p-4 flex items-center gap-4">
      <div className={toneClass}>{icon}</div>
      <div>
        <div className="text-xs uppercase tracking-wide text-slate-500">
          {label}
        </div>
        <div className="text-2xl font-semibold text-slate-900">{value}</div>
        {subtitle ? (
          <div className="text-xs text-slate-500">{subtitle}</div>
        ) : null}
      </div>
    </div>
  );
}

export default function StatusTiles() {
  const snapshot = useSnapshotStore((s) => s.snapshot);
  const filterMode = useFilterStore((s) => s.mode);
  const filterSelected = useFilterStore((s) => s.selected);
  if (!snapshot) {
    return (
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {[0, 1, 2, 3].map((i) => (
          <div
            key={i}
            className="bg-white rounded-lg border border-slate-200 p-4 h-20 animate-pulse"
          />
        ))}
      </div>
    );
  }

  const ctx = buildFilterContext(snapshot, filterMode, filterSelected);
  const agentsScoped = snapshot.agents.filter(
    (a) => ctx.mode === "all" || isAgentVisible(ctx, a.agent_uid),
  );
  const linksScoped = snapshot.link_states.filter((l) =>
    isEdgeVisible(ctx, l.source_agent_uid, l.target_agent_uid),
  );
  const alertsScoped = snapshot.recent_alerts.filter((a) =>
    isEdgeVisible(ctx, a.source_agent_uid, a.target_agent_uid),
  );

  const activeAgents = agentsScoped.filter((a) => a.state === "active");
  const upLinks = linksScoped.filter((l) => l.state === "up").length;
  const nonUpLinks = linksScoped.filter(
    (l) => l.state === "down" || l.state === "degraded",
  ).length;
  // Pending enrollments are pre-agent — no uid to filter by; always show.
  const pending = snapshot.pending_enrollments.length;
  const alerts1h = alertsScoped.length;

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
      <Tile
        label="Agents"
        value={activeAgents.length}
        subtitle={`${agentsScoped.length} total`}
        icon={<Users className="w-6 h-6" />}
        tone="neutral"
      />
      <Tile
        label="Links up"
        value={upLinks}
        subtitle={`${linksScoped.length} total`}
        icon={<CheckCircle2 className="w-6 h-6" />}
        tone={nonUpLinks === 0 ? "ok" : "warn"}
      />
      <Tile
        label="Pending"
        value={pending}
        subtitle={pending ? "need approval" : "none"}
        icon={<Clock className="w-6 h-6" />}
        tone={pending ? "warn" : "neutral"}
      />
      <Tile
        label="Alerts (1h)"
        value={alerts1h}
        icon={<AlertTriangle className="w-6 h-6" />}
        tone={alerts1h ? "alert" : "neutral"}
      />
    </div>
  );
}
