import { Pencil, Plus, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  addBssidToAp,
  createAccessPoint,
  deleteAccessPoint,
  listUnassignedBssids,
  removeBssidFromAp,
  updateAccessPoint,
} from "../api/endpoints";
import type {
  AccessPointCreate,
  AccessPointUpdate,
  AccessPointView,
  UnassignedBssidView,
} from "../api/types";
import { useSnapshotStore } from "../store/snapshot";
import { formatRelativeFromMs } from "../utils/time";

const BSSID_RE = /^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$/;

interface EditorState {
  id: number | null;
  name: string;
  location: string;
  notes: string;
  // Only used on create — existing APs manage BSSIDs via the row UI after save.
  bssidsOnCreate: string[];
  bssidDraft: string;
}

function emptyEditor(): EditorState {
  return {
    id: null,
    name: "",
    location: "",
    notes: "",
    bssidsOnCreate: [],
    bssidDraft: "",
  };
}

function editorFromRow(r: AccessPointView): EditorState {
  return {
    id: r.id,
    name: r.name,
    location: r.location ?? "",
    notes: r.notes ?? "",
    bssidsOnCreate: [],
    bssidDraft: "",
  };
}

export default function AccessPointsPage() {
  const snapshot = useSnapshotStore((s) => s.snapshot);
  const rows = snapshot?.access_points ?? [];

  const [editor, setEditor] = useState<EditorState | null>(null);
  const [busyId, setBusyId] = useState<number | "new" | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Unassigned BSSIDs — fetched on demand; not in the snapshot.
  const [unassigned, setUnassigned] = useState<UnassignedBssidView[]>([]);
  const [unassignedLoading, setUnassignedLoading] = useState(false);
  const [assignPicks, setAssignPicks] = useState<Record<string, string>>({}); // bssid -> ap_id (or "__new__")
  const [busyBssid, setBusyBssid] = useState<string | null>(null);

  const refreshUnassigned = useCallback(async () => {
    setUnassignedLoading(true);
    try {
      const r = await listUnassignedBssids();
      setUnassigned(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load unassigned");
    } finally {
      setUnassignedLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshUnassigned();
  }, [refreshUnassigned]);

  // Poll the unassigned list every 10s while the page is mounted — BSSIDs
  // come and go as agents roam.
  useEffect(() => {
    const t = setInterval(() => refreshUnassigned(), 10_000);
    return () => clearInterval(t);
  }, [refreshUnassigned]);

  const sorted = useMemo(
    () => [...rows].sort((a, b) => a.name.localeCompare(b.name)),
    [rows],
  );

  const hostnameOf = (uid: string) =>
    snapshot?.agents.find((a) => a.agent_uid === uid)?.hostname ?? uid.slice(0, 8);

  // -----------------------------
  // Editor (create / rename AP)
  // -----------------------------
  const openNew = () => {
    setError(null);
    setEditor(emptyEditor());
  };
  const openEdit = (r: AccessPointView) => {
    setError(null);
    setEditor(editorFromRow(r));
  };
  const close = () => setEditor(null);

  const addEditorBssid = () => {
    if (!editor) return;
    const b = editor.bssidDraft.trim().toLowerCase();
    if (!BSSID_RE.test(b)) {
      setError("bssid must be aa:bb:cc:dd:ee:ff");
      return;
    }
    if (editor.bssidsOnCreate.includes(b)) {
      setEditor({ ...editor, bssidDraft: "" });
      return;
    }
    setError(null);
    setEditor({
      ...editor,
      bssidsOnCreate: [...editor.bssidsOnCreate, b],
      bssidDraft: "",
    });
  };

  const onSubmitEditor = async () => {
    if (!editor) return;
    const name = editor.name.trim();
    if (!name) return setError("name required");
    const location = editor.location.trim() || null;
    const notes = editor.notes.trim() || null;
    setBusyId(editor.id ?? "new");
    setError(null);
    try {
      if (editor.id === null) {
        const body: AccessPointCreate = {
          name,
          bssids: editor.bssidsOnCreate,
          location,
          notes,
        };
        await createAccessPoint(body);
      } else {
        const body: AccessPointUpdate = { name, location, notes };
        await updateAccessPoint(editor.id, body);
      }
      setEditor(null);
      await refreshUnassigned();
    } catch (e) {
      setError(e instanceof Error ? e.message : "save failed");
    } finally {
      setBusyId(null);
    }
  };

  const onDelete = async (r: AccessPointView) => {
    if (
      !window.confirm(`Delete AP "${r.name}" (and remove its ${r.bssids.length} BSSID mapping(s))?`)
    )
      return;
    setError(null);
    setBusyId(r.id);
    try {
      await deleteAccessPoint(r.id);
      await refreshUnassigned();
    } catch (e) {
      setError(e instanceof Error ? e.message : "delete failed");
    } finally {
      setBusyId(null);
    }
  };

  const onRemoveBssid = async (r: AccessPointView, bssid: string) => {
    if (!window.confirm(`Detach ${bssid} from "${r.name}"?`)) return;
    setBusyBssid(bssid);
    setError(null);
    try {
      await removeBssidFromAp(r.id, bssid);
      await refreshUnassigned();
    } catch (e) {
      setError(e instanceof Error ? e.message : "detach failed");
    } finally {
      setBusyBssid(null);
    }
  };

  // -----------------------------
  // Assign unassigned → AP
  // -----------------------------
  const onAssign = async (bssid: string) => {
    const pick = assignPicks[bssid];
    if (!pick) return;
    setError(null);
    setBusyBssid(bssid);
    try {
      if (pick === "__new__") {
        // Prompt for a name; keep it cheap (window.prompt is fine for home-lab UX).
        const name = window.prompt(`Name for new AP (BSSID ${bssid}):`);
        if (!name || !name.trim()) return;
        await createAccessPoint({
          name: name.trim(),
          bssids: [bssid],
        });
      } else {
        await addBssidToAp(Number(pick), bssid);
      }
      setAssignPicks((p) => {
        const { [bssid]: _, ...rest } = p;
        return rest;
      });
      await refreshUnassigned();
    } catch (e) {
      setError(e instanceof Error ? e.message : "assign failed");
    } finally {
      setBusyBssid(null);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-slate-900">Access Points</h1>
          <p className="text-sm text-slate-500">
            Tag each observed BSSID to a named AP. Vendors like Ruckus broadcast
            multiple BSSIDs per radio/SSID — attach them all to one AP so the
            Trends view can collapse them into a single color.
          </p>
        </div>
        <button
          onClick={openNew}
          className="inline-flex items-center gap-1 px-3 py-1.5 rounded bg-sky-500 text-white hover:bg-sky-600 text-sm"
        >
          <Plus className="w-4 h-4" /> Add AP
        </button>
      </div>

      {error && !editor ? (
        <div className="px-4 py-2 bg-rose-50 text-rose-700 text-sm rounded">
          {error}
        </div>
      ) : null}

      {/* Unassigned BSSIDs */}
      <section className="bg-white rounded-lg border border-slate-200 overflow-hidden">
        <header className="px-4 py-2 border-b border-slate-200 flex items-center justify-between">
          <h2 className="text-sm font-medium text-slate-900">
            Unassigned BSSIDs
          </h2>
          <span className="text-xs text-slate-500">
            {unassignedLoading ? "refreshing…" : `${unassigned.length} seen`}
          </span>
        </header>
        {unassigned.length === 0 ? (
          <div className="px-4 py-6 text-sm text-slate-500 text-center">
            Every observed BSSID is assigned to an AP.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-xs text-slate-500 uppercase bg-slate-50">
              <tr>
                <th className="px-4 py-2 text-left font-medium">BSSID</th>
                <th className="px-4 py-2 text-left font-medium">SSID</th>
                <th className="px-4 py-2 text-left font-medium">Seen by</th>
                <th className="px-4 py-2 text-left font-medium">Last seen</th>
                <th className="px-4 py-2 text-right font-medium">Assign to</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {unassigned.map((u) => (
                <tr key={u.bssid} className="hover:bg-slate-50">
                  <td className="px-4 py-2 font-mono text-slate-700">{u.bssid}</td>
                  <td className="px-4 py-2 text-slate-700">
                    {u.last_ssid ?? "—"}
                  </td>
                  <td className="px-4 py-2 text-xs text-slate-500">
                    {u.agent_uids.map(hostnameOf).join(", ")}
                  </td>
                  <td className="px-4 py-2 text-slate-500">
                    {formatRelativeFromMs(u.last_seen_ms)}
                  </td>
                  <td className="px-4 py-2 text-right">
                    <div className="inline-flex items-center gap-1">
                      <select
                        value={assignPicks[u.bssid] ?? ""}
                        onChange={(e) =>
                          setAssignPicks((p) => ({
                            ...p,
                            [u.bssid]: e.target.value,
                          }))
                        }
                        className="border border-slate-200 rounded px-1.5 py-0.5 text-xs"
                      >
                        <option value="">— pick AP —</option>
                        {sorted.map((ap) => (
                          <option key={ap.id} value={ap.id}>
                            {ap.name}
                          </option>
                        ))}
                        <option value="__new__">+ New AP…</option>
                      </select>
                      <button
                        onClick={() => onAssign(u.bssid)}
                        disabled={
                          busyBssid === u.bssid || !assignPicks[u.bssid]
                        }
                        className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs text-sky-700 bg-sky-50 hover:bg-sky-100 disabled:opacity-50"
                      >
                        assign
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {/* APs */}
      <section className="bg-white rounded-lg border border-slate-200 overflow-hidden">
        <header className="px-4 py-2 border-b border-slate-200 flex items-center justify-between">
          <h2 className="text-sm font-medium text-slate-900">Access points</h2>
          <span className="text-xs text-slate-500">{sorted.length} total</span>
        </header>
        <table className="w-full text-sm">
          <thead className="text-xs text-slate-500 uppercase bg-slate-50">
            <tr>
              <th className="px-4 py-2 text-left font-medium">Name</th>
              <th className="px-4 py-2 text-left font-medium">BSSIDs</th>
              <th className="px-4 py-2 text-left font-medium">Location</th>
              <th className="px-4 py-2 text-left font-medium">Notes</th>
              <th className="px-4 py-2 text-left font-medium">Updated</th>
              <th className="px-4 py-2 text-right font-medium">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {sorted.map((r) => (
              <tr key={r.id} className="hover:bg-slate-50 align-top">
                <td className="px-4 py-2 font-medium text-slate-900">
                  {r.name}
                </td>
                <td className="px-4 py-2">
                  {r.bssids.length === 0 ? (
                    <span className="text-slate-400 text-xs">none</span>
                  ) : (
                    <ul className="space-y-0.5">
                      {r.bssids.map((b) => (
                        <li
                          key={b}
                          className="flex items-center gap-1 text-xs font-mono text-slate-700"
                        >
                          <span>{b}</span>
                          <button
                            onClick={() => onRemoveBssid(r, b)}
                            disabled={busyBssid === b}
                            title="Detach this BSSID from the AP"
                            className="inline-flex items-center text-slate-400 hover:text-rose-600 disabled:opacity-50"
                          >
                            <X className="w-3 h-3" />
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </td>
                <td className="px-4 py-2 text-slate-600">
                  {r.location ?? "—"}
                </td>
                <td className="px-4 py-2 text-slate-500 text-xs max-w-xs truncate">
                  {r.notes ?? "—"}
                </td>
                <td className="px-4 py-2 text-slate-500">
                  {formatRelativeFromMs(r.updated_at)}
                </td>
                <td className="px-4 py-2 text-right">
                  <button
                    onClick={() => openEdit(r)}
                    disabled={busyId === r.id}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs text-slate-600 hover:bg-slate-100 disabled:opacity-50"
                  >
                    <Pencil className="w-3 h-3" /> edit
                  </button>
                  <button
                    onClick={() => onDelete(r)}
                    disabled={busyId === r.id}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs text-rose-600 hover:bg-rose-50 disabled:opacity-50 ml-2"
                  >
                    <Trash2 className="w-3 h-3" /> delete
                  </button>
                </td>
              </tr>
            ))}
            {sorted.length === 0 ? (
              <tr>
                <td
                  colSpan={6}
                  className="px-4 py-6 text-center text-slate-500 text-sm"
                >
                  No access points yet.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </section>

      {editor ? (
        <div className="fixed inset-0 z-20 grid place-items-center bg-black/30">
          <div className="bg-white rounded-lg shadow-lg border border-slate-200 w-full max-w-lg p-5 space-y-4">
            <div>
              <h2 className="text-sm font-semibold text-slate-900">
                {editor.id === null ? "Add access point" : "Edit access point"}
              </h2>
              {editor.id !== null ? (
                <p className="text-xs text-slate-500 mt-1">
                  Manage BSSIDs from the main table — add them from the
                  Unassigned list or remove inline.
                </p>
              ) : null}
            </div>
            {error ? (
              <div className="px-3 py-2 bg-rose-50 text-rose-700 text-sm rounded">
                {error}
              </div>
            ) : null}
            <div className="grid grid-cols-2 gap-3 text-sm">
              <label className="col-span-2">
                <div className="text-xs text-slate-500 mb-1">Name</div>
                <input
                  value={editor.name}
                  onChange={(e) =>
                    setEditor({ ...editor, name: e.target.value })
                  }
                  className="w-full border border-slate-200 rounded px-2 py-1"
                  placeholder="Attic AP"
                />
              </label>
              <label className="col-span-2">
                <div className="text-xs text-slate-500 mb-1">Location</div>
                <input
                  value={editor.location}
                  onChange={(e) =>
                    setEditor({ ...editor, location: e.target.value })
                  }
                  className="w-full border border-slate-200 rounded px-2 py-1"
                  placeholder="Optional — e.g. upstairs hallway"
                />
              </label>
              <label className="col-span-2">
                <div className="text-xs text-slate-500 mb-1">Notes</div>
                <textarea
                  value={editor.notes}
                  onChange={(e) =>
                    setEditor({ ...editor, notes: e.target.value })
                  }
                  rows={3}
                  className="w-full border border-slate-200 rounded px-2 py-1"
                  placeholder="Optional — model, channel, etc."
                />
              </label>
              {editor.id === null ? (
                <div className="col-span-2">
                  <div className="text-xs text-slate-500 mb-1">
                    BSSIDs (optional — you can also assign later from the
                    Unassigned list)
                  </div>
                  <div className="flex items-center gap-1">
                    <input
                      value={editor.bssidDraft}
                      onChange={(e) =>
                        setEditor({ ...editor, bssidDraft: e.target.value })
                      }
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          addEditorBssid();
                        }
                      }}
                      className="flex-1 border border-slate-200 rounded px-2 py-1 font-mono"
                      placeholder="aa:bb:cc:dd:ee:ff"
                    />
                    <button
                      onClick={addEditorBssid}
                      className="px-2 py-1 rounded text-xs text-slate-700 bg-slate-100 hover:bg-slate-200"
                    >
                      add
                    </button>
                  </div>
                  {editor.bssidsOnCreate.length > 0 ? (
                    <ul className="mt-2 space-y-0.5">
                      {editor.bssidsOnCreate.map((b) => (
                        <li
                          key={b}
                          className="flex items-center gap-1 text-xs font-mono text-slate-700"
                        >
                          <span>{b}</span>
                          <button
                            onClick={() =>
                              setEditor({
                                ...editor,
                                bssidsOnCreate: editor.bssidsOnCreate.filter(
                                  (x) => x !== b,
                                ),
                              })
                            }
                            className="text-slate-400 hover:text-rose-600"
                          >
                            <X className="w-3 h-3" />
                          </button>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              ) : null}
            </div>
            <div className="flex justify-end gap-2">
              <button
                onClick={close}
                disabled={busyId !== null}
                className="px-3 py-1.5 rounded text-sm text-slate-600 hover:bg-slate-100 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={onSubmitEditor}
                disabled={busyId !== null}
                className="px-3 py-1.5 rounded bg-sky-500 text-white hover:bg-sky-600 text-sm disabled:opacity-50"
              >
                {busyId !== null ? "Saving…" : "Save"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
