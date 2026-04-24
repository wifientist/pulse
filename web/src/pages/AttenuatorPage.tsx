import {
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
  cancelAttenuatorRun,
  createAttenuatorPreset,
  deleteAttenuatorPreset,
  listAttenuatorPresets,
  listAttenuatorRuns,
  listRuckusAps,
  setApRuckusSerial,
  startAttenuatorRun,
  updateAttenuatorPreset,
} from "../api/endpoints";
import type {
  AttenuatorParticipant,
  AttenuatorPresetView,
  RuckusApView,
  ToolRunView,
  TxPowerValue,
} from "../api/types";
import { useSnapshotStore } from "../store/snapshot";
import { formatRelativeFromMs } from "../utils/time";

const TX_POWER_VALUES: TxPowerValue[] = [
  "MAX",
  "-1", "-2", "-3", "-4", "-5", "-6", "-7", "-8", "-9", "-10",
  "-11", "-12", "-13", "-14", "-15", "-16", "-17", "-18", "-19",
  "-20", "-21", "-22", "-23",
  "MIN",
];

const STATE_BADGE: Record<string, string> = {
  running: "bg-sky-50 text-sky-700",
  completed: "bg-emerald-50 text-emerald-700",
  failed: "bg-rose-50 text-rose-700",
  cancelled: "bg-slate-100 text-slate-500",
};

export default function AttenuatorPage() {
  const snapshot = useSnapshotStore((s) => s.snapshot);
  const accessPoints = snapshot?.access_points ?? [];
  const activeRun = snapshot?.active_tool_run ?? null;

  const [ruckusAps, setRuckusAps] = useState<RuckusApView[]>([]);
  const [ruckusLoading, setRuckusLoading] = useState(false);
  const [ruckusError, setRuckusError] = useState<string | null>(null);

  const [presets, setPresets] = useState<AttenuatorPresetView[]>([]);
  const [runs, setRuns] = useState<ToolRunView[]>([]);
  const [busy, setBusy] = useState<number | "new" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refreshPresets = useCallback(async () => {
    try {
      setPresets(await listAttenuatorPresets());
    } catch (e) {
      setError(e instanceof Error ? e.message : "presets load failed");
    }
  }, []);

  const refreshRuns = useCallback(async () => {
    try {
      setRuns(await listAttenuatorRuns());
    } catch (e) {
      setError(e instanceof Error ? e.message : "runs load failed");
    }
  }, []);

  useEffect(() => {
    refreshPresets();
    refreshRuns();
  }, [refreshPresets, refreshRuns]);

  // Refresh the runs table every 5 seconds while a run is active so the
  // user sees it tick through states without a manual reload.
  useEffect(() => {
    if (!activeRun) return;
    const t = setInterval(() => refreshRuns(), 5000);
    return () => clearInterval(t);
  }, [activeRun, refreshRuns]);

  const onSyncRuckus = async () => {
    setRuckusLoading(true);
    setRuckusError(null);
    try {
      setRuckusAps(await listRuckusAps());
    } catch (e) {
      setRuckusError(
        e instanceof Error ? e.message : "Ruckus sync failed",
      );
    } finally {
      setRuckusLoading(false);
    }
  };

  const onMap = async (apId: number, serial: string | null) => {
    try {
      await setApRuckusSerial(apId, serial || null);
      await onSyncRuckus();
    } catch (e) {
      setError(e instanceof Error ? e.message : "mapping failed");
    }
  };

  const onStartPreset = async (presetId: number) => {
    if (!window.confirm("Start this preset? Agents' powers will change.")) {
      return;
    }
    setError(null);
    try {
      await startAttenuatorRun({ preset_id: presetId });
      await refreshRuns();
    } catch (e) {
      setError(e instanceof Error ? e.message : "start failed");
    }
  };

  const onCancelRun = async (id: number) => {
    if (!window.confirm("Cancel the run and restore powers?")) return;
    try {
      await cancelAttenuatorRun(id);
      await refreshRuns();
    } catch (e) {
      setError(e instanceof Error ? e.message : "cancel failed");
    }
  };

  const onDeletePreset = async (id: number) => {
    if (!window.confirm("Delete this preset?")) return;
    try {
      await deleteAttenuatorPreset(id);
      await refreshPresets();
    } catch (e) {
      setError(e instanceof Error ? e.message : "delete failed");
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold text-slate-900">Attenuator</h1>
        <p className="text-sm text-slate-500">
          Ramp Ruckus APs' transmit power — drop some, raise others, force
          client roams. Each ramp step polls until Ruckus confirms; on
          completion/cancel we restore pre-run power automatically.
        </p>
      </div>

      {error ? (
        <div className="px-4 py-2 bg-rose-50 text-rose-700 text-sm rounded">
          {error}
        </div>
      ) : null}

      {/* Active run banner */}
      {activeRun ? (
        <ActiveRunBanner run={activeRun} onCancel={() => onCancelRun(activeRun.id)} />
      ) : null}

      {/* Ruckus AP → Pulse AP mapping */}
      <section className="bg-white rounded-lg border border-slate-200 overflow-hidden">
        <header className="px-4 py-2 border-b border-slate-200 flex items-center justify-between">
          <div>
            <h2 className="text-sm font-medium text-slate-900">
              Ruckus AP mapping
            </h2>
            <p className="text-xs text-slate-500">
              Sync the APs in your Ruckus venue, then attach each to a Pulse
              Access Point so presets can refer to them by name.
            </p>
          </div>
          <button
            onClick={onSyncRuckus}
            disabled={ruckusLoading}
            className="inline-flex items-center gap-1 px-3 py-1.5 rounded bg-sky-500 text-white hover:bg-sky-600 text-sm disabled:opacity-50"
          >
            <RefreshCw
              className={`w-4 h-4 ${ruckusLoading ? "animate-spin" : ""}`}
            />
            Sync from Ruckus
          </button>
        </header>
        {ruckusError ? (
          <div className="px-4 py-2 bg-rose-50 text-rose-700 text-sm">
            {ruckusError}
          </div>
        ) : null}
        {ruckusAps.length === 0 ? (
          <div className="px-4 py-6 text-sm text-slate-500 text-center">
            {ruckusLoading
              ? "Fetching…"
              : "Click Sync from Ruckus to list your venue's APs."}
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-xs text-slate-500 uppercase bg-slate-50">
              <tr>
                <th className="px-4 py-2 text-left font-medium">Ruckus AP</th>
                <th className="px-4 py-2 text-left font-medium">Serial</th>
                <th className="px-4 py-2 text-left font-medium">Model</th>
                <th className="px-4 py-2 text-left font-medium">Status</th>
                <th className="px-4 py-2 text-left font-medium">Mapped to Pulse AP</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {ruckusAps.map((r) => (
                <tr key={r.serial} className="hover:bg-slate-50">
                  <td className="px-4 py-2 font-medium text-slate-900">
                    {r.name ?? "—"}
                  </td>
                  <td className="px-4 py-2 font-mono text-slate-500">
                    {r.serial}
                  </td>
                  <td className="px-4 py-2 text-slate-600">{r.model ?? "—"}</td>
                  <td className="px-4 py-2 text-slate-600">{r.status ?? "—"}</td>
                  <td className="px-4 py-2">
                    <select
                      value={r.mapped_ap_id ?? ""}
                      onChange={(e) => {
                        const id = e.target.value ? Number(e.target.value) : null;
                        // Unmap: clear serial on whichever AP currently owns it.
                        if (id === null && r.mapped_ap_id != null) {
                          onMap(r.mapped_ap_id, null);
                          return;
                        }
                        if (id != null) onMap(id, r.serial);
                      }}
                      className="border border-slate-200 rounded px-2 py-0.5 text-xs"
                    >
                      <option value="">— unmapped —</option>
                      {accessPoints.map((ap) => (
                        <option key={ap.id} value={ap.id}>
                          {ap.name}
                        </option>
                      ))}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {/* Presets */}
      <PresetsSection
        presets={presets}
        mappedAps={accessPoints.filter((a) => a.ruckus_serial)}
        busyKey={busy}
        setBusyKey={setBusy}
        onRefresh={refreshPresets}
        onStart={onStartPreset}
        onDelete={onDeletePreset}
        disableStart={activeRun !== null}
        setError={setError}
      />

      {/* Run history */}
      <RunHistorySection runs={runs} />
    </div>
  );
}

// ----------------------------------------------------------------------

function ActiveRunBanner({
  run,
  onCancel,
}: {
  run: NonNullable<ReturnType<typeof useSnapshotStore.getState>["snapshot"]>["active_tool_run"];
  onCancel: () => void;
}) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  if (!run) return null;
  const remaining = Math.max(0, run.ends_at - now);
  const mm = Math.floor(remaining / 60000);
  const ss = Math.floor((remaining % 60000) / 1000);
  const cfg = (run.config ?? {}) as {
    name?: string;
    participants?: Array<{ ap_name?: string; target_value?: string; direction?: string; start_value?: string }>;
  };
  return (
    <section className="bg-sky-50 border border-sky-200 rounded-lg p-4 space-y-2">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold text-sky-900">
            Active run: {cfg.name ?? `run #${run.id}`}
          </div>
          <div className="text-xs text-sky-800">
            {mm}m {String(ss).padStart(2, "0")}s window remaining
          </div>
        </div>
        <button
          onClick={onCancel}
          className="inline-flex items-center gap-1 px-3 py-1.5 rounded bg-white border border-amber-300 text-amber-800 hover:bg-amber-50 text-sm"
        >
          <Square className="w-3 h-3" /> Cancel + restore
        </button>
      </div>
      {cfg.participants && cfg.participants.length > 0 ? (
        <ul className="text-xs text-slate-700 grid grid-cols-1 md:grid-cols-2 gap-1">
          {cfg.participants.map((p, i) => (
            <li key={i} className="font-mono">
              {p.ap_name}: {p.start_value} → {p.target_value} ({p.direction})
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

// ----------------------------------------------------------------------

function PresetsSection({
  presets,
  mappedAps,
  busyKey,
  setBusyKey,
  onRefresh,
  onStart,
  onDelete,
  disableStart,
  setError,
}: {
  presets: AttenuatorPresetView[];
  mappedAps: { id: number; name: string; ruckus_serial: string | null }[];
  busyKey: number | "new" | null;
  setBusyKey: (v: number | "new" | null) => void;
  onRefresh: () => Promise<void>;
  onStart: (id: number) => Promise<void>;
  onDelete: (id: number) => Promise<void>;
  disableStart: boolean;
  setError: (v: string | null) => void;
}) {
  const [editor, setEditor] = useState<EditorState | null>(null);

  const openNew = () =>
    setEditor({
      id: null,
      name: "",
      radio: "5g",
      step_size_db: 3,
      step_interval_s: 10,
      participants: [],
      boost_participants: true,
      instant: false,
    });
  const openEdit = (p: AttenuatorPresetView) =>
    setEditor({
      id: p.id,
      name: p.name,
      radio: p.radio,
      step_size_db: p.step_size_db,
      step_interval_s: p.step_interval_s,
      participants: p.participants.slice(),
      boost_participants: p.boost_participants,
      instant: p.instant,
    });

  const onSubmit = async () => {
    if (!editor) return;
    const name = editor.name.trim();
    if (!name) return setError("name required");
    if (editor.participants.length === 0) {
      return setError("at least one participant required");
    }
    setBusyKey(editor.id ?? "new");
    setError(null);
    try {
      if (editor.id == null) {
        await createAttenuatorPreset({
          name,
          radio: editor.radio,
          step_size_db: editor.step_size_db,
          step_interval_s: editor.step_interval_s,
          participants: editor.participants,
          boost_participants: editor.boost_participants,
          instant: editor.instant,
        });
      } else {
        await updateAttenuatorPreset(editor.id, {
          name,
          radio: editor.radio,
          step_size_db: editor.step_size_db,
          step_interval_s: editor.step_interval_s,
          participants: editor.participants,
          boost_participants: editor.boost_participants,
          instant: editor.instant,
        });
      }
      setEditor(null);
      await onRefresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "save failed");
    } finally {
      setBusyKey(null);
    }
  };

  return (
    <section className="bg-white rounded-lg border border-slate-200 overflow-hidden">
      <header className="px-4 py-2 border-b border-slate-200 flex items-center justify-between">
        <h2 className="text-sm font-medium text-slate-900">Presets</h2>
        <button
          onClick={openNew}
          disabled={mappedAps.length < 2}
          title={
            mappedAps.length < 2
              ? "Map at least two Ruckus APs to Pulse APs first"
              : "Create a new preset"
          }
          className="inline-flex items-center gap-1 px-3 py-1.5 rounded bg-sky-500 text-white hover:bg-sky-600 text-sm disabled:opacity-50"
        >
          <Plus className="w-4 h-4" /> New preset
        </button>
      </header>
      {presets.length === 0 ? (
        <div className="px-4 py-6 text-sm text-slate-500 text-center">
          No presets yet.
        </div>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-xs text-slate-500 uppercase bg-slate-50">
            <tr>
              <th className="px-4 py-2 text-left font-medium">Name</th>
              <th className="px-4 py-2 text-left font-medium">Radio</th>
              <th className="px-4 py-2 text-left font-medium">Step</th>
              <th className="px-4 py-2 text-left font-medium">Participants</th>
              <th className="px-4 py-2 text-right font-medium">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {presets.map((p) => (
              <tr key={p.id} className="hover:bg-slate-50 align-top">
                <td className="px-4 py-2 font-medium text-slate-900">
                  {p.name}
                  {p.instant ? (
                    <span className="ml-1 text-[10px] text-violet-700 bg-violet-50 rounded px-1 py-0.5">
                      instant
                    </span>
                  ) : null}
                  {p.boost_participants ? (
                    <span className="ml-1 text-[10px] text-amber-700 bg-amber-50 rounded px-1 py-0.5">
                      boost-on-start
                    </span>
                  ) : null}
                </td>
                <td className="px-4 py-2 text-slate-600 uppercase">{p.radio}</td>
                <td className="px-4 py-2 text-slate-600 font-mono text-xs">
                  {p.instant ? (
                    <span className="text-slate-400">—</span>
                  ) : (
                    <>−{p.step_size_db} dB / {p.step_interval_s}s</>
                  )}
                </td>
                <td className="px-4 py-2 text-xs text-slate-700">
                  <ul className="space-y-0.5">
                    {p.participants.map((part, i) => {
                      const ap = mappedAps.find((a) => a.id === part.ap_id);
                      return (
                        <li key={i} className="font-mono">
                          {ap?.name ?? `#${part.ap_id}`} →{" "}
                          <strong>{part.target_value}</strong> ({part.direction})
                        </li>
                      );
                    })}
                  </ul>
                </td>
                <td className="px-4 py-2 text-right">
                  <button
                    onClick={() => onStart(p.id)}
                    disabled={disableStart}
                    title={
                      disableStart
                        ? "Another run is already active"
                        : "Start this preset"
                    }
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs text-emerald-700 bg-emerald-50 hover:bg-emerald-100 disabled:opacity-50"
                  >
                    <Play className="w-3 h-3" /> start
                  </button>
                  <button
                    onClick={() => openEdit(p)}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs text-slate-600 hover:bg-slate-100 ml-2"
                  >
                    <Pencil className="w-3 h-3" /> edit
                  </button>
                  <button
                    onClick={() => onDelete(p.id)}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs text-rose-600 hover:bg-rose-50 ml-2"
                  >
                    <Trash2 className="w-3 h-3" /> delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {editor ? (
        <PresetEditor
          editor={editor}
          mappedAps={mappedAps}
          onChange={setEditor}
          onSubmit={onSubmit}
          onCancel={() => setEditor(null)}
          busy={busyKey !== null}
        />
      ) : null}
    </section>
  );
}

interface EditorState {
  id: number | null;
  name: string;
  radio: "5g" | "24g" | "6g";
  step_size_db: number;
  step_interval_s: number;
  participants: AttenuatorParticipant[];
  boost_participants: boolean;
  instant: boolean;
}

function PresetEditor({
  editor,
  mappedAps,
  onChange,
  onSubmit,
  onCancel,
  busy,
}: {
  editor: EditorState;
  mappedAps: { id: number; name: string }[];
  onChange: (e: EditorState) => void;
  onSubmit: () => void;
  onCancel: () => void;
  busy: boolean;
}) {
  const addParticipant = () => {
    const used = new Set(editor.participants.map((p) => p.ap_id));
    const firstFree = mappedAps.find((a) => !used.has(a.id));
    if (!firstFree) return;
    onChange({
      ...editor,
      participants: [
        ...editor.participants,
        { ap_id: firstFree.id, direction: "drop", target_value: "-3" },
      ],
    });
  };
  const updateParticipant = (
    idx: number,
    patch: Partial<AttenuatorParticipant>,
  ) =>
    onChange({
      ...editor,
      participants: editor.participants.map((p, i) =>
        i === idx ? { ...p, ...patch } : p,
      ),
    });
  const removeParticipant = (idx: number) =>
    onChange({
      ...editor,
      participants: editor.participants.filter((_, i) => i !== idx),
    });

  return (
    <div className="fixed inset-0 z-20 grid place-items-center bg-black/30">
      <div className="bg-white rounded-lg shadow-lg border border-slate-200 w-full max-w-2xl p-5 space-y-4">
        <h2 className="text-sm font-semibold text-slate-900">
          {editor.id == null ? "New preset" : "Edit preset"}
        </h2>
        <div className="grid grid-cols-4 gap-3 text-sm">
          <label className="col-span-2">
            <div className="text-xs text-slate-500 mb-1">Name</div>
            <input
              value={editor.name}
              onChange={(e) => onChange({ ...editor, name: e.target.value })}
              className="w-full border border-slate-200 rounded px-2 py-1"
              placeholder="Atrium → Kitchen roam test"
            />
          </label>
          <label className="col-span-1">
            <div className="text-xs text-slate-500 mb-1">Radio</div>
            <select
              value={editor.radio}
              onChange={(e) =>
                onChange({
                  ...editor,
                  radio: e.target.value as EditorState["radio"],
                })
              }
              className="w-full border border-slate-200 rounded px-2 py-1"
            >
              <option value="5g">5 GHz</option>
              <option value="24g">2.4 GHz</option>
              <option value="6g">6 GHz</option>
            </select>
          </label>
          <label className="col-span-1">
            <div className="text-xs text-slate-500 mb-1">
              Step
              {editor.instant ? (
                <span className="ml-1 text-slate-400">(disabled)</span>
              ) : null}
            </div>
            <div className="inline-flex items-center gap-1">
              <span className="text-slate-400">−</span>
              <input
                type="number"
                min={1}
                max={23}
                value={editor.step_size_db}
                disabled={editor.instant}
                onChange={(e) =>
                  onChange({
                    ...editor,
                    step_size_db: Math.max(1, Math.min(23, Number(e.target.value))),
                  })
                }
                className="w-12 border border-slate-200 rounded px-2 py-1 disabled:bg-slate-50 disabled:text-slate-400"
              />
              <span className="text-slate-400">dB /</span>
              <input
                type="number"
                min={1}
                max={120}
                value={editor.step_interval_s}
                disabled={editor.instant}
                onChange={(e) =>
                  onChange({
                    ...editor,
                    step_interval_s: Math.max(
                      1,
                      Math.min(120, Number(e.target.value)),
                    ),
                  })
                }
                className="w-14 border border-slate-200 rounded px-2 py-1 disabled:bg-slate-50 disabled:text-slate-400"
              />
              <span className="text-slate-400">s</span>
            </div>
          </label>

          <label className="col-span-4 inline-flex items-center gap-2">
            <input
              type="checkbox"
              checked={editor.instant}
              onChange={(e) =>
                onChange({ ...editor, instant: e.target.checked })
              }
            />
            <span>
              <span className="font-medium">Instant</span>{" "}
              <span className="text-slate-500 text-xs">
                — skip the ramp; apply target txPower in one step and leave it
                in place (no auto-restore). Cancel still restores.
              </span>
            </span>
          </label>

          <label className="col-span-4 inline-flex items-center gap-2">
            <input
              type="checkbox"
              checked={editor.boost_participants}
              onChange={(e) =>
                onChange({ ...editor, boost_participants: e.target.checked })
              }
            />
            <span>Boost agents currently associated to participating APs</span>
          </label>
        </div>

        <div>
          <div className="flex items-center justify-between mb-1">
            <div className="text-xs text-slate-500">Participants</div>
            <button
              onClick={addParticipant}
              disabled={editor.participants.length >= mappedAps.length}
              className="inline-flex items-center gap-1 text-xs text-sky-600 hover:text-sky-800 disabled:opacity-50"
            >
              <Plus className="w-3 h-3" /> add AP
            </button>
          </div>
          {editor.participants.length === 0 ? (
            <div className="text-xs text-slate-400 italic px-2 py-1">
              No APs yet. Add at least one.
            </div>
          ) : (
            <ul className="space-y-1">
              {editor.participants.map((p, i) => (
                <li
                  key={i}
                  className="grid grid-cols-[1fr_auto_auto_auto] gap-2 items-center text-sm"
                >
                  <select
                    value={p.ap_id}
                    onChange={(e) =>
                      updateParticipant(i, { ap_id: Number(e.target.value) })
                    }
                    className="border border-slate-200 rounded px-2 py-1 text-sm"
                  >
                    {mappedAps.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                      </option>
                    ))}
                  </select>
                  <select
                    value={p.direction}
                    onChange={(e) =>
                      updateParticipant(i, {
                        direction: e.target.value as "drop" | "raise",
                      })
                    }
                    className="border border-slate-200 rounded px-2 py-1 text-xs"
                  >
                    <option value="drop">drop</option>
                    <option value="raise">raise</option>
                  </select>
                  <select
                    value={p.target_value}
                    onChange={(e) =>
                      updateParticipant(i, {
                        target_value: e.target.value as TxPowerValue,
                      })
                    }
                    className="border border-slate-200 rounded px-2 py-1 text-xs font-mono"
                  >
                    {TX_POWER_VALUES.map((v) => (
                      <option key={v} value={v}>
                        target {v}
                      </option>
                    ))}
                  </select>
                  <button
                    onClick={() => removeParticipant(i)}
                    className="inline-flex items-center justify-center w-6 h-6 rounded text-slate-400 hover:text-rose-600 hover:bg-rose-50"
                  >
                    <X className="w-3 h-3" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="flex justify-end gap-2">
          <button
            onClick={onCancel}
            disabled={busy}
            className="px-3 py-1.5 rounded text-sm text-slate-600 hover:bg-slate-100 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={onSubmit}
            disabled={busy}
            className="px-3 py-1.5 rounded bg-sky-500 text-white hover:bg-sky-600 text-sm disabled:opacity-50"
          >
            {busy ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------

function RunHistorySection({ runs }: { runs: ToolRunView[] }) {
  return (
    <section className="bg-white rounded-lg border border-slate-200 overflow-hidden">
      <header className="px-4 py-2 border-b border-slate-200">
        <h2 className="text-sm font-medium text-slate-900">Run history</h2>
      </header>
      {runs.length === 0 ? (
        <div className="px-4 py-6 text-sm text-slate-500 text-center">
          No runs yet.
        </div>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-xs text-slate-500 uppercase bg-slate-50">
            <tr>
              <th className="px-4 py-2 text-left font-medium">Name</th>
              <th className="px-4 py-2 text-left font-medium">State</th>
              <th className="px-4 py-2 text-left font-medium">Started</th>
              <th className="px-4 py-2 text-left font-medium">Window</th>
              <th className="px-4 py-2 text-left font-medium">Error</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {runs.map((r) => {
              const cfg = (r.config ?? {}) as { name?: string };
              const badge = STATE_BADGE[r.state] ?? "bg-slate-100 text-slate-700";
              return (
                <tr key={r.id} className="hover:bg-slate-50">
                  <td className="px-4 py-2 font-medium text-slate-900">
                    {cfg.name ?? `run #${r.id}`}
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`px-2 py-0.5 rounded text-xs font-medium ${badge}`}
                    >
                      {r.state}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-slate-500">
                    {formatRelativeFromMs(r.started_at)}
                  </td>
                  <td className="px-4 py-2 text-slate-500">
                    {Math.round((r.ends_at - r.started_at) / 1000)}s
                  </td>
                  <td className="px-4 py-2 text-xs text-rose-600 truncate max-w-md">
                    {r.error ?? ""}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}
