import type { LinkState } from "../api/types";

export interface EdgeStyle {
  stroke: string;
  strokeWidth: number;
  strokeDasharray?: string;
  labelBg: string;
  labelText: string;
}

const UP: EdgeStyle = {
  stroke: "#10b981", // emerald-500
  strokeWidth: 2,
  labelBg: "bg-emerald-50",
  labelText: "text-emerald-700",
};

const DEGRADED: EdgeStyle = {
  stroke: "#f59e0b", // amber-500
  strokeWidth: 2.5,
  labelBg: "bg-amber-50",
  labelText: "text-amber-700",
};

const DOWN: EdgeStyle = {
  stroke: "#f43f5e", // rose-500
  strokeWidth: 3,
  labelBg: "bg-rose-50",
  labelText: "text-rose-700",
};

const UNKNOWN: EdgeStyle = {
  stroke: "#94a3b8", // slate-400
  strokeWidth: 1.5,
  strokeDasharray: "6 4",
  labelBg: "bg-slate-100",
  labelText: "text-slate-600",
};

export function styleForState(state: LinkState | string | undefined): EdgeStyle {
  switch (state) {
    case "up":
      return UP;
    case "degraded":
      return DEGRADED;
    case "down":
      return DOWN;
    case "unknown":
    case undefined:
    case null:
    default:
      return UNKNOWN;
  }
}

export function stateBadgeClass(state: LinkState | string | undefined): string {
  const s = styleForState(state);
  return `inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${s.labelBg} ${s.labelText}`;
}
