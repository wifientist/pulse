import { Sliders } from "lucide-react";
import { Link } from "react-router-dom";

// Tools landing page. One card per available tool. Add more cards here as
// new tool pages arrive — the route structure is /tools/<name>.
export default function ToolsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold text-slate-900">Tools</h1>
        <p className="text-sm text-slate-500">
          Active network utilities — drive the infrastructure to produce a
          specific scenario (forced roam, attenuation ramp, etc.) while
          Pulse records the effect through its agents.
        </p>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        <Link
          to="/tools/attenuator"
          className="block bg-white rounded-lg border border-slate-200 p-4 hover:border-sky-300 hover:shadow-sm transition"
        >
          <div className="flex items-center gap-2 text-slate-900">
            <Sliders className="w-5 h-5 text-sky-500" />
            <span className="font-semibold">Attenuator</span>
          </div>
          <p className="text-sm text-slate-500 mt-2">
            Ramp the txPower of selected Ruckus APs on a schedule — drop
            some, raise others, trigger client roams, and watch the result
            in Trends.
          </p>
        </Link>
      </div>
    </div>
  );
}
