import { Pause, Play, Plus } from "lucide-react";
import { useMemo, useState } from "react";

import AddAgentDialog from "../components/AddAgentDialog";
import AgentsTable from "../components/AgentsTable";
import EnrollmentTokensSection from "../components/EnrollmentTokensSection";
import PassiveTargetsSection from "../components/PassiveTargetsSection";
import PendingEnrollmentsSection from "../components/PendingEnrollmentsSection";
import { pauseAllAgents, resumeAllAgents } from "../api/endpoints";
import { useSnapshotStore } from "../store/snapshot";

export default function AgentsPage() {
  const [addOpen, setAddOpen] = useState(false);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkErr, setBulkErr] = useState<string | null>(null);

  const snapshot = useSnapshotStore((s) => s.snapshot);
  const { activeCount, pausedCount } = useMemo(() => {
    let active = 0;
    let paused = 0;
    for (const a of snapshot?.agents ?? []) {
      if (a.state === "revoked") continue;
      if (a.paused) paused++;
      else active++;
    }
    return { activeCount: active, pausedCount: paused };
  }, [snapshot?.agents]);

  const onPauseAll = async () => {
    if (
      !window.confirm(
        `Pause all ${activeCount} active agents? They'll keep polling but stop pinging until you resume.`,
      )
    )
      return;
    setBulkErr(null);
    setBulkBusy(true);
    try {
      await pauseAllAgents();
    } catch (e) {
      setBulkErr(e instanceof Error ? e.message : "pause-all failed");
    } finally {
      setBulkBusy(false);
    }
  };

  const onResumeAll = async () => {
    setBulkErr(null);
    setBulkBusy(true);
    try {
      await resumeAllAgents();
    } catch (e) {
      setBulkErr(e instanceof Error ? e.message : "resume-all failed");
    } finally {
      setBulkBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-slate-900">Agents</h1>
        <div className="flex items-center gap-2">
          {pausedCount > 0 ? (
            <button
              onClick={onResumeAll}
              disabled={bulkBusy}
              title={`Resume all ${pausedCount} paused agent(s)`}
              className="inline-flex items-center gap-1 px-3 py-1.5 rounded text-sm border border-amber-300 bg-amber-50 text-amber-800 hover:bg-amber-100 disabled:opacity-50"
            >
              <Play className="w-4 h-4" /> Resume all ({pausedCount})
            </button>
          ) : null}
          {activeCount > 0 ? (
            <button
              onClick={onPauseAll}
              disabled={bulkBusy}
              title={`Pause all ${activeCount} active agent(s)`}
              className="inline-flex items-center gap-1 px-3 py-1.5 rounded text-sm border border-slate-200 text-slate-700 hover:bg-slate-50 disabled:opacity-50"
            >
              <Pause className="w-4 h-4" /> Pause all
            </button>
          ) : null}
          <button
            onClick={() => setAddOpen(true)}
            className="inline-flex items-center gap-1 px-3 py-1.5 rounded bg-sky-500 text-white hover:bg-sky-600 text-sm"
          >
            <Plus className="w-4 h-4" /> Add agent
          </button>
        </div>
      </div>
      {bulkErr ? (
        <div className="px-3 py-2 bg-rose-50 text-rose-700 text-sm rounded">
          {bulkErr}
        </div>
      ) : null}
      <PendingEnrollmentsSection />
      <AgentsTable />
      <PassiveTargetsSection />
      <EnrollmentTokensSection />
      <AddAgentDialog open={addOpen} onClose={() => setAddOpen(false)} />
    </div>
  );
}
