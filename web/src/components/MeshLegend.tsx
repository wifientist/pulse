/**
 * Tiny legend explaining what the edge styles in the mesh diagram mean. Rendered as a
 * thin strip below the diagram. Stroke colors come from utils/edgeColor to stay in
 * sync with the actual render.
 */

import { styleForState } from "../utils/edgeColor";

function EdgeSample({
  state,
  animated,
}: {
  state: string;
  animated?: boolean;
}) {
  const style = styleForState(state);
  return (
    <svg width="40" height="10" className="inline-block align-middle">
      <defs>
        <marker
          id={`arrow-${state}`}
          viewBox="0 0 10 10"
          refX="8"
          refY="5"
          markerWidth="6"
          markerHeight="6"
          orient="auto-start-reverse"
        >
          <path d="M 0 0 L 10 5 L 0 10 z" fill={style.stroke} />
        </marker>
      </defs>
      <line
        x1="2"
        y1="5"
        x2="32"
        y2="5"
        stroke={style.stroke}
        strokeWidth={style.strokeWidth}
        strokeDasharray={style.strokeDasharray}
        markerEnd={`url(#arrow-${state})`}
      >
        {animated ? (
          <animate
            attributeName="stroke-dashoffset"
            values="0;-12"
            dur="0.8s"
            repeatCount="indefinite"
          />
        ) : null}
      </line>
    </svg>
  );
}

export default function MeshLegend() {
  return (
    <div className="bg-white rounded-lg border border-slate-200 px-4 py-2 flex items-center gap-5 text-xs text-slate-600 flex-wrap">
      <span className="font-medium text-slate-700">Legend:</span>
      <span className="inline-flex items-center gap-2">
        <EdgeSample state="up" animated /> up (&lt; degraded thresholds)
      </span>
      <span className="inline-flex items-center gap-2">
        <EdgeSample state="degraded" /> degraded (loss or p95 over threshold)
      </span>
      <span className="inline-flex items-center gap-2">
        <EdgeSample state="down" /> down (100% loss for dwell window)
      </span>
      <span className="inline-flex items-center gap-2">
        <EdgeSample state="unknown" /> unknown (insufficient samples yet)
      </span>
      <span className="ml-auto text-slate-500">
        Arrow = ping direction · drag endpoint to any handle to reroute
      </span>
    </div>
  );
}
