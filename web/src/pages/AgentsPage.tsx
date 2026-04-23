import { Plus } from "lucide-react";
import { useState } from "react";

import AddAgentDialog from "../components/AddAgentDialog";
import AgentsTable from "../components/AgentsTable";
import EnrollmentTokensSection from "../components/EnrollmentTokensSection";
import PassiveTargetsSection from "../components/PassiveTargetsSection";
import PendingEnrollmentsSection from "../components/PendingEnrollmentsSection";

export default function AgentsPage() {
  const [addOpen, setAddOpen] = useState(false);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-slate-900">Agents</h1>
        <button
          onClick={() => setAddOpen(true)}
          className="inline-flex items-center gap-1 px-3 py-1.5 rounded bg-sky-500 text-white hover:bg-sky-600 text-sm"
        >
          <Plus className="w-4 h-4" /> Add agent
        </button>
      </div>
      <PendingEnrollmentsSection />
      <AgentsTable />
      <PassiveTargetsSection />
      <EnrollmentTokensSection />
      <AddAgentDialog open={addOpen} onClose={() => setAddOpen(false)} />
    </div>
  );
}
