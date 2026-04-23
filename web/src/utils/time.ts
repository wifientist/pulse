export function formatRelativeFromMs(ts: number | null, nowMs = Date.now()): string {
  if (ts == null) return "never";
  const diffMs = nowMs - ts;
  if (diffMs < 0) return "just now";
  if (diffMs < 60_000) return `${Math.round(diffMs / 1000)}s ago`;
  if (diffMs < 3_600_000) return `${Math.round(diffMs / 60_000)}m ago`;
  if (diffMs < 86_400_000) return `${Math.round(diffMs / 3_600_000)}h ago`;
  return `${Math.round(diffMs / 86_400_000)}d ago`;
}

export function formatMs(n: number | null | undefined, digits = 2): string {
  if (n == null) return "—";
  if (n < 1) return `${n.toFixed(digits)} ms`;
  return `${n.toFixed(digits)} ms`;
}

export function formatPct(n: number | null | undefined, digits = 1): string {
  if (n == null) return "—";
  return `${n.toFixed(digits)}%`;
}

export function formatAbsolute(ts: number): string {
  return new Date(ts).toLocaleString();
}
