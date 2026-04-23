import { apiDelete, apiGet, apiPatch, apiPost } from "./client";
import type {
  AccessPointCreate,
  AccessPointUpdate,
  AccessPointView,
  AgentView,
  AlertView,
  ApproveBody,
  BoostView,
  NewEnrollmentTokenBody,
  NewEnrollmentTokenResponse,
  PassiveTargetCreate,
  PassiveTargetUpdate,
  PassiveTargetView,
  PendingEnrollmentView,
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
