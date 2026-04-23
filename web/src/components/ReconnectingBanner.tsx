import { useSnapshotStore } from "../store/snapshot";

export default function ReconnectingBanner() {
  const status = useSnapshotStore((s) => s.status);
  if (status !== "reconnecting") return null;
  return (
    <div className="bg-amber-100 text-amber-900 text-sm text-center py-1">
      Reconnecting to server…
    </div>
  );
}
