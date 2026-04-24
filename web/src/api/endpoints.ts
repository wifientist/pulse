import { apiDelete, apiGet, apiPatch, apiPost } from "./client";
import type {
  AccessPointCreate,
  AccessPointUpdate,
  AccessPointView,
  AgentView,
  AirspaceResponse,
  AlertView,
  ApproveBody,
  AttenuatorPresetCreate,
  AttenuatorPresetUpdate,
  AttenuatorPresetView,
  BoostView,
  MonitoredSsidView,
  NewEnrollmentTokenBody,
  NewEnrollmentTokenResponse,
  PassiveTargetCreate,
  PassiveTargetUpdate,
  PassiveTargetView,
  PendingEnrollmentView,
  RuckusApView,
  StartRunBody,
  ToolRunDetailView,
  ToolRunView,
  TrendResponse,
  UnassignedBssidView,
} from "./types";

// Read endpoints exist for ad-hoc use; in practice pages consume the SSE snapshot.

export const listAgents = () => apiGet<AgentView[]>("/v1/admin/agents");

export const listPendingEnrollments = () =>
  apiGet<PendingEnrollmentView[]>("/v1/admin/enrollments/pending");

export const listRecentAlerts = (sinceTs?: number, limit = 100) => {
  const params = new URLSearchParams();
  if (sinceTs !== undefined) params.set("since_ts", String(sinceTs));
  params.set("limit", String(limit));
  return apiGet<AlertView[]>(`/v1/admin/alerts?${params}`);
};

// Mutations — pages call these; next SSE tick reflects the state change.

export const approveEnrollment = (id: number, body: ApproveBody = {}) =>
  apiPost<{ agent_id: number; agent_uid: string }>(
    `/v1/admin/enrollments/${id}/approve`,
    body,
  );

export const rejectEnrollment = (id: number) =>
  apiPost<void>(`/v1/admin/enrollments/${id}/reject`);

export const mintEnrollmentToken = (body: NewEnrollmentTokenBody) =>
  apiPost<NewEnrollmentTokenResponse>("/v1/admin/enrollment-tokens", body);

export const revokeEnrollmentToken = (id: number) =>
  apiDelete(`/v1/admin/enrollment-tokens/${id}`);

export const setInterfaceRole = (
  agentId: number,
  mac: string,
  role: string,
) =>
  apiPost<AgentView>(`/v1/admin/agents/${agentId}/set-interface-role`, {
    mac,
    role,
  });

export const triggerDhcpRenew = (agentId: number, ifaceName: string) =>
  apiPost<{ command_id: number }>(`/v1/admin/agents/${agentId}/dhcp-renew`, {
    iface_name: ifaceName,
  });

export const upgradeAgent = (agentId: number) =>
  apiPost<{ command_id: number; target_version: string }>(
    `/v1/admin/agents/${agentId}/upgrade`,
  );

// --- Access points ---------------------------------------------------

export const listAccessPoints = () =>
  apiGet<AccessPointView[]>("/v1/admin/access-points");

export const createAccessPoint = (body: AccessPointCreate) =>
  apiPost<AccessPointView>("/v1/admin/access-points", body);

export const updateAccessPoint = (id: number, body: AccessPointUpdate) =>
  apiPatch<AccessPointView>(`/v1/admin/access-points/${id}`, body);

export const deleteAccessPoint = (id: number) =>
  apiDelete(`/v1/admin/access-points/${id}`);

export const listUnassignedBssids = () =>
  apiGet<UnassignedBssidView[]>("/v1/admin/access-points/unassigned-bssids");

export const addBssidToAp = (apId: number, bssid: string) =>
  apiPost<AccessPointView>(`/v1/admin/access-points/${apId}/bssids`, { bssid });

export const removeBssidFromAp = (apId: number, bssid: string) =>
  apiDelete(`/v1/admin/access-points/${apId}/bssids/${bssid}`);

// --- Passive targets -------------------------------------------------

export const createPassiveTarget = (body: PassiveTargetCreate) =>
  apiPost<PassiveTargetView>("/v1/admin/passive-targets", body);

export const updatePassiveTarget = (id: number, body: PassiveTargetUpdate) =>
  apiPatch<PassiveTargetView>(`/v1/admin/passive-targets/${id}`, body);

export const deletePassiveTarget = (id: number) =>
  apiDelete(`/v1/admin/passive-targets/${id}`);

// --- Boost -----------------------------------------------------------

export const startBoost = (agentId: number, durationS: number) =>
  apiPost<BoostView>(`/v1/admin/agents/${agentId}/boost`, {
    duration_s: durationS,
  });

export const cancelBoost = (agentId: number) =>
  apiDelete(`/v1/admin/agents/${agentId}/boost`);

// --- Tools: Attenuator -----------------------------------------------

export const listRuckusAps = () =>
  apiGet<RuckusApView[]>("/v1/admin/tools/attenuator/ruckus-aps");

export const setApRuckusSerial = (apId: number, ruckus_serial: string | null) =>
  apiPatch<void>(`/v1/admin/tools/attenuator/aps/${apId}`, { ruckus_serial });

export const listAttenuatorPresets = () =>
  apiGet<AttenuatorPresetView[]>("/v1/admin/tools/attenuator/presets");

export const createAttenuatorPreset = (body: AttenuatorPresetCreate) =>
  apiPost<AttenuatorPresetView>("/v1/admin/tools/attenuator/presets", body);

export const updateAttenuatorPreset = (
  id: number,
  body: AttenuatorPresetUpdate,
) =>
  apiPatch<AttenuatorPresetView>(
    `/v1/admin/tools/attenuator/presets/${id}`,
    body,
  );

export const deleteAttenuatorPreset = (id: number) =>
  apiDelete(`/v1/admin/tools/attenuator/presets/${id}`);

export const listAttenuatorRuns = () =>
  apiGet<ToolRunView[]>("/v1/admin/tools/attenuator/runs");

export const getAttenuatorRun = (id: number) =>
  apiGet<ToolRunDetailView>(`/v1/admin/tools/attenuator/runs/${id}`);

export const startAttenuatorRun = (body: StartRunBody) =>
  apiPost<ToolRunView>("/v1/admin/tools/attenuator/runs", body);

export const cancelAttenuatorRun = (id: number) =>
  apiPost<ToolRunView>(`/v1/admin/tools/attenuator/runs/${id}/cancel`);

// --- Monitored SSIDs + Airspace --------------------------------------

export const listMonitoredSsids = () =>
  apiGet<MonitoredSsidView[]>("/v1/admin/monitored-ssids");

export const createMonitoredSsid = (ssid: string) =>
  apiPost<MonitoredSsidView>("/v1/admin/monitored-ssids", { ssid });

export const deleteMonitoredSsid = (id: number) =>
  apiDelete(`/v1/admin/monitored-ssids/${id}`);

export const getAirspace = (
  agentUid: string,
  sinceTs: number,
  untilTs: number,
) => {
  const p = new URLSearchParams();
  p.set("agent_uid", agentUid);
  p.set("since_ts", String(sinceTs));
  p.set("until_ts", String(untilTs));
  return apiGet<AirspaceResponse>(`/v1/admin/airspace?${p}`);
};

// --- Trends ----------------------------------------------------------

export const getTrends = (
  sourceUid: string,
  targetUid: string,
  sinceTs: number,
  untilTs?: number,
  granularity?: "auto" | "minute" | "hour",
) => {
  const p = new URLSearchParams();
  p.set("source_uid", sourceUid);
  p.set("target_uid", targetUid);
  p.set("since_ts", String(sinceTs));
  if (untilTs != null) p.set("until_ts", String(untilTs));
  if (granularity) p.set("granularity", granularity);
  return apiGet<TrendResponse>(`/v1/admin/trends?${p}`);
};
