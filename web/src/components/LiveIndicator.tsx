import { useEffect, useState } from "react";

import { useSnapshotStore } from "../store/snapshot";
import { formatRelativeFromMs } from "../utils/time";

export default function LiveIndicator() {
  const status = useSnapshotStore((s) => s.status);
  const lastEventAt = useSnapshotStore((s) => s.lastEventAt);
  const [, forceTick] = useState(0);

  // Re-render once per second so "updated Ns ago" label stays fresh.
  useEffect(() => {
    const handle = window.setInterval(() => forceTick((t) => t + 1), 1000);
    return () => window.clearInterval(handle);
  }, []);

  const dotClass =
    status === "live"
      ? "bg-emerald-500"
      : status === "reconnecting" || status === "connecting"
        ? "bg-amber-500"
        : "bg-slate-400";

  const label = (() => {
    if (!lastEventAt) return status;
    if (status === "live") return `updated ${formatRelativeFromMs(lastEventAt)}`;
    return status;
  })();

  return (
    <span className="inline-flex items-center gap-2 text-xs text-slate-500">
      <span className={`inline-block w-2 h-2 rounded-full ${dotClass}`} />
      {label}
    </span>
  );
}
