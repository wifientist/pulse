import { Handle, Position, type NodeProps } from "@xyflow/react";
import { memo } from "react";

import type { MeshNodeData, MeshNodeType } from "../utils/derive";

const stateToBorder: Record<string, string> = {
  active: "border-l-emerald-500",
  pending: "border-l-amber-500",
  stale: "border-l-amber-500",
  revoked: "border-l-slate-400",
};

/**
 * One handle per side (top/right/bottom/left). The parent ReactFlow runs in
 * ConnectionMode.Loose so every handle accepts either a source or target drop —
 * this is what makes drag-to-reroute actually land on drop. Handle ids are the
 * bare position names; the mesh-diagram layer persists the user's chosen handles
 * per edge id.
 */
const HANDLE_CLASS =
  "!w-3 !h-3 !border-2 !border-white !bg-slate-400 hover:!bg-sky-500 transition-colors";

function MeshNode({ data }: NodeProps<MeshNodeType>) {
  const a = (data as MeshNodeData).agent;
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

export default memo(MeshNode);
