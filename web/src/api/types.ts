// Mirrors the Pydantic view-models exposed by the admin API and the SSE snapshot
// payload. Keep these in sync with server/pulse_server/routers/*.py and
// server/pulse_server/routers/events.py::build_snapshot.

export type LinkState = "up" | "degraded" | "down" | "unknown";
export type AgentState = "pending" | "active" | "stale" | "revoked";

export type InterfaceRole = "test" | "management" | "ignored" | "unknown";

export interface InterfaceView {
  id: number;
  mac: string;
  current_ip: string | null;
  iface_name: string | null;
  role: InterfaceRole | string;
  ssid: string | null;
  bssid: string | null;
  signal_dbm: number | null;
  first_seen: number;
  last_seen: number;
}

export interface AccessPointView {
  id: number;
  name: string;
  bssids: string[];
  location: string | null;
  notes: string | null;
  created_at: number;
  updated_at: number;
}

export interface UnassignedBssidView {
  bssid: string;
  last_seen_ms: number;
  last_ssid: string | null;
  agent_uids: string[];
}

export interface BoostView {
  agent_id: number;
  agent_uid: string;
  started_at: number;
  expires_at: number;
}

export interface PassiveTargetView {
  id: number;
  name: string;
  ip: string;
  notes: string | null;
  enabled: boolean;
  created_at: number;
  updated_at: number;
}

export interface PassiveTargetCreate {
  name: string;
  ip: string;
  notes?: string | null;
  enabled?: boolean;
}

export interface PassiveTargetUpdate {
  name?: string;
  ip?: string;
  notes?: string | null;
  enabled?: boolean;
}

export interface PassiveLinkStateView {
  source_agent_uid: string;
  passive_target_id: number;
  state: LinkState | string;
  since_ts: number;
  loss_pct_1m: number | null;
  rtt_p95_1m: number | null;
}

export interface TrendPoint {
  ts_ms: number;
  sent: number;
  lost: number;
  loss_pct: number | null;
  rtt_avg_ms: number | null;
  rtt_min_ms: number | null;
  rtt_max_ms: number | null;
  rtt_p50_ms: number | null;
  rtt_p95_ms: number | null;
  rtt_p99_ms: number | null;
  jitter_ms: number | null;
}

export interface TrendSummary {
  sent_total: number;
  lost_total: number;
  loss_pct: number | null;
  rtt_avg_ms: number | null;
  rtt_p95_ms: number | null;
  point_count: number;
}

export interface WirelessTrendPoint {
  ts_ms: number;
  ssid: string | null;
  bssid: string | null;
  signal_dbm: number | null;
}

export interface WirelessRoam {
  ts_ms: number;
  from_bssid: string;
  to_bssid: string;
}

export interface WirelessTrendSeries {
  agent_uid: string;
  hostname: string | null;
  iface_name: string | null;
  points: WirelessTrendPoint[];
  roams: WirelessRoam[];
}

export interface TrendResponse {
  source_agent_uid: string;
  target_agent_uid: string;
  since_ts: number;
  until_ts: number;
  granularity: "raw" | "minute" | "hour" | string;
  bucket_s: number | null;
  points: TrendPoint[];
  summary: TrendSummary;
  wireless: WirelessTrendSeries[];
}

export interface AgentView {
  id: number;
  agent_uid: string;
  hostname: string;
  os: string;
  state: AgentState | string;
  primary_ip: string | null;
  management_ip: string | null;
  poll_interval_s: number;
  ping_interval_s: number;
  created_at: number;
  approved_at: number | null;
  last_poll_at: number | null;
  agent_version: string | null;
  caps: Record<string, unknown>;
  interfaces: InterfaceView[];
}

export interface PendingEnrollmentView {
  id: number;
  agent_uid: string;
  reported_hostname: string;
  reported_ip: string;
  caps: Record<string, unknown>;
  created_at: number;
  approved: boolean;
}

export interface PeerAssignmentView {
  id: number;
  source_agent_uid: string;
  target_agent_uid: string;
  target_ip: string;
  interval_s: number | null;
  enabled: boolean;
  source: string;
}

export interface LinkStateView {
  source_agent_uid: string;
  target_agent_uid: string;
  state: LinkState | string;
  since_ts: number;
  loss_pct_1m: number | null;
  rtt_p95_1m: number | null;
}

export interface AlertView {
  id: number;
  source_agent_uid: string;
  target_agent_uid: string;
  from_state: string;
  to_state: string;
  at_ts: number;
  context: Record<string, unknown>;
}

export interface EnrollmentTokenView {
  id: number;
  label: string;
  created_at: number;
  expires_at: number | null;
  uses_remaining: number | null;
  revoked: boolean;
}

export interface SnapshotEvent {
  emitted_at: number;
  agents: AgentView[];
  pending_enrollments: PendingEnrollmentView[];
  peer_assignments: PeerAssignmentView[];
  link_states: LinkStateView[];
  recent_alerts: AlertView[];
  enrollment_tokens: EnrollmentTokenView[];
  access_points: AccessPointView[];
  boosts: BoostView[];
  passive_targets: PassiveTargetView[];
  passive_link_states: PassiveLinkStateView[];
}

export interface AccessPointCreate {
  name: string;
  bssids?: string[];
  location?: string | null;
  notes?: string | null;
}

export interface AccessPointUpdate {
  name?: string;
  location?: string | null;
  notes?: string | null;
}

// ---- mutation payloads --------------------------------------------------

export interface ApproveBody {
  poll_interval_s?: number | null;
  ping_interval_s?: number | null;
}

export interface NewEnrollmentTokenBody {
  label: string;
  expires_at?: number | null;
  uses_remaining?: number | null;
}

export interface NewEnrollmentTokenResponse {
  id: number;
  label: string;
  created_at: number;
  expires_at: number | null;
  uses_remaining: number | null;
  revoked: boolean;
  plaintext: string;
}
