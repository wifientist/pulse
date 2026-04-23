import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Target } from "lucide-react";
import { memo } from "react";

import type { MeshNodeData, MeshNodeType } from "../utils/derive";

const stateToBorder: Record<string, string> = {
  active: "border-l-emerald-500",
  pending: "border-l-amber-500",
  stale: "border-l-amber-500",
  revoked: "border-l-slate-400",
};

// Passive nodes use a dashed border and a different border-color per state so
// they're visually distinct from agent nodes at a glance.
const passiveStateBorder: Record<string, string> = {
  up: "border-emerald-500",
  degraded: "border-amber-500",
  down: "border-rose-500",
  unknown: "border-slate-300",
};

const HANDLE_CLASS =
  "!w-3 !h-3 !border-2 !border-white !bg-slate-400 hover:!bg-sky-500 transition-colors";

function AgentBody({ data }: { data: MeshNodeData }) {
  const a = data.agent!;
  const border = stateToBorder[a.state] ?? "border-l-slate-400";
  return (
    <div
      className={`bg-white shadow rounded-md border border-slate-200 border-l-4 ${border} px-3 py-2 min-w-[180px] relative`}
    >
      <Handle type="source" position={Position.Top} id="top" className={HANDLE_CLASS} />
      <Handle type="source" position={Position.Right} id="right" className={HANDLE_CLASS} />
      <Handle type="source" position={Position.Bottom} id="bottom" className={HANDLE_CLASS} />
      <Handle type="source" position={Position.Left} id="left" className={HANDLE_CLASS} />
      <div className="text-sm font-semibold text-slate-900 truncate">
        {a.hostname}
      </div>
      <div className="text-xs text-slate-500 font-mono truncate">
        {a.primary_ip ?? "—"}
      </div>
      {a.management_ip ? (
        <div className="text-[10px] text-slate-400 font-mono truncate">
          mgmt {a.management_ip}
        </div>
      ) : null}
    </div>
  );
}

function PassiveBody({ data }: { data: MeshNodeData }) {
  const pt = data.passive!;
  const state = (data.passive_state as string) ?? "unknown";
  const border = passiveStateBorder[state] ?? "border-slate-300";
  return (
    <div
      className={`bg-slate-50 rounded-md border-2 border-dashed ${border} px-3 py-2 min-w-[160px] relative`}
    >
      <Handle type="source" position={Position.Top} id="top" className={HANDLE_CLASS} />
      <Handle type="source" position={Position.Right} id="right" className={HANDLE_CLASS} />
      <Handle type="source" position={Position.Bottom} id="bottom" className={HANDLE_CLASS} />
      <Handle type="source" position={Position.Left} id="left" className={HANDLE_CLASS} />
      <div className="flex items-center gap-1 text-sm font-semibold text-slate-900 truncate">
        <Target className="w-3.5 h-3.5 text-slate-500 shrink-0" />
        <span className="truncate">{pt.name}</span>
      </div>
      <div className="text-xs text-slate-500 font-mono truncate">{pt.ip}</div>
      <div className="text-[10px] text-slate-400 truncate">passive target</div>
    </div>
  );
}

function MeshNode({ data }: NodeProps<MeshNodeType>) {
  const d = data as MeshNodeData;
  return d.passive ? <PassiveBody data={d} /> : <AgentBody data={d} />;
}

export default memo(MeshNode);
