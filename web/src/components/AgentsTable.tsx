import {
  ArrowUpCircle,
  ChevronDown,
  ChevronRight,
  RefreshCw,
} from "lucide-react";
import { Fragment, useState } from "react";

import {
  setInterfaceRole,
  triggerDhcpRenew,
  upgradeAgent,
} from "../api/endpoints";
import type { AgentView, InterfaceView } from "../api/types";
import { useSnapshotStore } from "../store/snapshot";
import { formatRelativeFromMs } from "../utils/time";

const agentStateClass: Record<string, string> = {
  active: "bg-emerald-50 text-emerald-700",
  pending: "bg-amber-50 text-amber-700",
  stale: "bg-amber-50 text-amber-700",
  revoked: "bg-slate-100 text-slate-500",
};

const roleClass: Record<string, string> = {
  test: "bg-emerald-50 text-emerald-700",
  management: "bg-sky-50 text-sky-700",
  ignored: "bg-slate-100 text-slate-500",
  unknown: "bg-amber-50 text-amber-700",
};

const ROLE_OPTIONS = ["test", "management", "ignored", "unknown"] as const;

function InterfacesPanel({
  agent,
  busyMac,
  onChangeRole,
  onRenew,
}: {
  agent: AgentView;
  busyMac: string | null;
  onChangeRole: (mac: string, role: string) => void;
  onRenew: (iface: string) => void;
}) {
  if (agent.interfaces.length === 0) {
    return (
      <div className="px-4 py-3 bg-slate-50 text-xs text-slate-500 border-t border-slate-100">
        No interfaces reported yet. Upgrade the agent (⇪ Upgrade) to 0.2.0+ to enable
        MAC-tracked interfaces.
      </div>
    );
  }
  return (
    <div className="bg-slate-50 border-t border-slate-100">
      <table className="w-full text-xs">
        <thead className="text-slate-500 uppercase">
          <tr>
            <th className="px-4 py-1.5 text-left font-medium">Role</th>
            <th className="px-4 py-1.5 text-left font-medium">Interface</th>
            <th className="px-4 py-1.5 text-left font-medium">MAC</th>
            <th className="px-4 py-1.5 text-left font-medium">Current IP</th>
            <th className="px-4 py-1.5 text-left font-medium">First seen</th>
            <th className="px-4 py-1.5 text-left font-medium">Last seen</th>
            <th className="px-4 py-1.5 text-left font-medium">Action</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {agent.interfaces.map((i: InterfaceView) => (
            <tr key={i.id}>
              <td className="px-4 py-1.5">
                <select
                  value={i.role}
                  onChange={(e) => onChangeRole(i.mac, e.target.value)}
                  disabled={busyMac === i.mac}
                  className={`font-medium rounded px-1.5 py-0.5 border-0 text-xs ${
                    roleClass[i.role] ?? "bg-slate-100 text-slate-700"
                  }`}
                  title="Classify: test (peers ping it), management, ignored, or unknown"
                >
                  {ROLE_OPTIONS.map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </select>
              </td>
              <td className="px-4 py-1.5 font-mono text-slate-700">
                {i.iface_name ?? "—"}
              </td>
              <td className="px-4 py-1.5 font-mono text-slate-500">{i.mac}</td>
              <td className="px-4 py-1.5 font-mono text-slate-700">
                {i.current_ip ?? "—"}
              </td>
              <td className="px-4 py-1.5 text-slate-500">
                {formatRelativeFromMs(i.first_seen)}
              </td>
              <td className="px-4 py-1.5 text-slate-500">
                {formatRelativeFromMs(i.last_seen)}
              </td>
              <td className="px-4 py-1.5">
                <button
                  type="button"
                  onClick={() => i.iface_name && onRenew(i.iface_name)}
                  disabled={busyMac === i.mac || !i.iface_name}
                  title="Force the agent to DHCP release+renew this interface"
                  className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs text-slate-600 hover:bg-slate-100 disabled:opacity-50"
                >
                  <RefreshCw className="w-3 h-3" /> renew
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function AgentsTable() {
  const snapshot = useSnapshotStore((s) => s.snapshot);
  const agents = snapshot?.agents ?? [];
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  const [busy, setBusy] = useState<{ agent: number; mac: string } | null>(null);
  const [upgradingAgent, setUpgradingAgent] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const toggle = (id: number) =>
    setExpanded((prev) => ({ ...prev, [id]: !prev[id] }));

  const onChangeRole = async (agentId: number, mac: string, role: string) => {
    setError(null);
    setBusy({ agent: agentId, mac });
    try {
      await setInterfaceRole(agentId, mac, role);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to set role");
    } finally {
      setBusy(null);
    }
  };

  const onRenew = async (agentId: number, mac: string, iface: string) => {
    setError(null);
    setNotice(null);
    setBusy({ agent: agentId, mac });
    try {
      await triggerDhcpRenew(agentId, iface);
      setNotice(`DHCP renew queued on ${iface}; watch for IP update in a few seconds.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to queue renew");
    } finally {
      setBusy(null);
    }
  };

  const onUpgrade = async (agent: AgentView) => {
    if (
      !window.confirm(
        `Upgrade ${agent.hostname} to the latest agent version?\n\n` +
          "The agent will download the source tarball, build a new venv, atomic-swap it " +
          "in, and restart itself (~30s). Rollback (manual): on the host, swap " +
          "/opt/pulse/src.prev and /opt/pulse/.venv.prev back.",
      )
    ) {
      return;
    }
    setError(null);
    setNotice(null);
    setUpgradingAgent(agent.id);
    try {
      const r = await upgradeAgent(agent.id);
      setNotice(
        `Upgrade to ${r.target_version} queued for ${agent.hostname}. Expect the agent to come back on the new version within ~30s.`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "upgrade failed");
    } finally {
      setUpgradingAgent(null);
    }
  };

  return (
    <section className="bg-white rounded-lg border border-slate-200 overflow-hidden">
      <header className="px-4 py-2 border-b border-slate-200 flex items-center justify-between">
        <h2 className="text-sm font-medium text-slate-900">Agents</h2>
        <span className="text-xs text-slate-500">{agents.length} total</span>
      </header>
      {error ? (
        <div className="px-4 py-2 bg-rose-50 text-rose-700 text-sm">{error}</div>
      ) : null}
      {notice ? (
        <div className="px-4 py-2 bg-sky-50 text-sky-700 text-sm">{notice}</div>
      ) : null}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-xs text-slate-500 uppercase bg-slate-50">
            <tr>
              <th className="w-8 px-2 py-2" />
              <th className="px-4 py-2 text-left font-medium">Hostname</th>
              <th className="px-4 py-2 text-left font-medium">State</th>
              <th className="px-4 py-2 text-left font-medium">Test IP</th>
              <th className="px-4 py-2 text-left font-medium">Mgmt IP</th>
              <th className="px-4 py-2 text-left font-medium">Last poll</th>
              <th className="px-4 py-2 text-left font-medium">Version</th>
              <th className="px-4 py-2 text-left font-medium">Ifaces</th>
              <th className="px-4 py-2 text-right font-medium">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {agents.map((a) => {
              const isOpen = !!expanded[a.id];
              return (
                <Fragment key={a.id}>
                  <tr className="hover:bg-slate-50">
                    <td
                      className="w-8 px-2 py-2 text-slate-400 text-center cursor-pointer"
                      onClick={() => toggle(a.id)}
                    >
                      {isOpen ? (
                        <ChevronDown className="w-4 h-4 inline" />
                      ) : (
                        <ChevronRight className="w-4 h-4 inline" />
                      )}
                    </td>
                    <td
                      className="px-4 py-2 font-medium text-slate-900 cursor-pointer"
                      onClick={() => toggle(a.id)}
                    >
                      {a.hostname}
                      <div className="text-xs text-slate-400 font-mono">
                        {a.agent_uid.slice(0, 8)}…
                      </div>
                    </td>
                    <td className="px-4 py-2">
                      <span
                        className={`px-2 py-0.5 rounded text-xs font-medium ${agentStateClass[a.state] ?? "bg-slate-100 text-slate-700"}`}
                      >
                        {a.state}
                      </span>
                    </td>
                    <td className="px-4 py-2 font-mono text-slate-700">
                      {a.primary_ip ?? "—"}
                    </td>
                    <td className="px-4 py-2 font-mono text-slate-500">
                      {a.management_ip ?? "—"}
                    </td>
                    <td className="px-4 py-2 text-slate-600">
                      {formatRelativeFromMs(a.last_poll_at)}
                    </td>
                    <td className="px-4 py-2 text-slate-500 font-mono">
                      {a.agent_version ?? "—"}
                    </td>
                    <td className="px-4 py-2 text-slate-600">
                      {a.interfaces.length}
                    </td>
                    <td className="px-4 py-2 text-right">
                      <button
                        type="button"
                        onClick={() => onUpgrade(a)}
                        disabled={
                          upgradingAgent === a.id || a.state !== "active"
                        }
                        title="Push latest agent version to this host"
                        className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs text-slate-600 hover:bg-slate-100 disabled:opacity-50"
                      >
                        <ArrowUpCircle className="w-3 h-3" /> upgrade
                      </button>
                    </td>
                  </tr>
                  {isOpen ? (
                    <tr>
                      <td colSpan={9} className="p-0">
                        <InterfacesPanel
                          agent={a}
                          busyMac={busy?.agent === a.id ? busy.mac : null}
                          onChangeRole={(mac, role) =>
                            onChangeRole(a.id, mac, role)
                          }
                          onRenew={(iface) => {
                            const mac =
                              a.interfaces.find((i) => i.iface_name === iface)
                                ?.mac ?? "";
                            onRenew(a.id, mac, iface);
                          }}
                        />
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              );
            })}
            {agents.length === 0 ? (
              <tr>
                <td
                  colSpan={9}
                  className="px-4 py-6 text-center text-slate-500 text-sm"
                >
                  No agents yet.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </section>
  );
}
