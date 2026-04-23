import { apiDelete, apiGet, apiPost } from "./client";
import type {
  AgentView,
  AlertView,
  ApproveBody,
  NewEnrollmentTokenBody,
  NewEnrollmentTokenResponse,
  PendingEnrollmentView,
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
