import { describe, expect, it } from "vitest";

import type {
  AgentView,
  PeerAssignmentView,
  LinkStateView,
  SnapshotEvent,
} from "../api/types";
import { buildMeshGraph } from "../utils/derive";

function agent(uid: string, overrides: Partial<AgentView> = {}): AgentView {
  return {
    id: 1,
    agent_uid: uid,
    hostname: `h-${uid.slice(0, 4)}`,
    os: "linux",
    state: "active",
    primary_ip: "10.20.1.1",
    management_ip: "10.0.99.1",
    poll_interval_s: 3,
    ping_interval_s: 2,
    created_at: 1,
    approved_at: 1,
    last_poll_at: 1,
    agent_version: "0.1.0",
    caps: {},
    interfaces: [],
    ...overrides,
  };
}

function peer(
  src: string,
  tgt: string,
  overrides: Partial<PeerAssignmentView> = {},
): PeerAssignmentView {
  return {
    id: 1,
    source_agent_uid: src,
    target_agent_uid: tgt,
    target_ip: "10.20.1.1",
    interval_s: null,
    enabled: true,
    source: "auto",
    ...overrides,
  };
}

function link(
  src: string,
  tgt: string,
  overrides: Partial<LinkStateView> = {},
): LinkStateView {
  return {
    source_agent_uid: src,
    target_agent_uid: tgt,
    state: "up",
    since_ts: 1,
    loss_pct_1m: 0,
    rtt_p95_1m: 1.5,
    ...overrides,
  };
}

function snap(overrides: Partial<SnapshotEvent> = {}): SnapshotEvent {
  return {
    emitted_at: 1,
    agents: [],
    pending_enrollments: [],
    peer_assignments: [],
    link_states: [],
    recent_alerts: [],
    enrollment_tokens: [],
    ...overrides,
  };
}

describe("buildMeshGraph", () => {
  it("produces one node per non-revoked agent", () => {
    const s = snap({
      agents: [
        agent("a"),
        agent("b"),
        agent("c", { state: "revoked" }),
      ],
    });
    const g = buildMeshGraph(s);
    expect(g.nodes.map((n) => n.id).sort()).toEqual(["a", "b"]);
  });

  it("drops peer assignments that are disabled or reference missing agents", () => {
    const s = snap({
      agents: [agent("a"), agent("b")],
      peer_assignments: [
        peer("a", "b"),
        peer("a", "ghost"),
        peer("b", "a", { enabled: false }),
      ],
    });
    const g = buildMeshGraph(s);
    expect(g.edges.map((e) => e.id)).toEqual(["a->b"]);
  });

  it("associates link_states with the correct edge by (src, tgt)", () => {
    const s = snap({
      agents: [agent("a"), agent("b")],
      peer_assignments: [peer("a", "b"), peer("b", "a")],
      link_states: [
        link("a", "b", { state: "down", loss_pct_1m: 100 }),
        link("b", "a", { state: "up" }),
      ],
    });
    const g = buildMeshGraph(s);
    const ab = g.edges.find((e) => e.id === "a->b")!;
    const ba = g.edges.find((e) => e.id === "b->a")!;
    expect(ab.data?.state).toBe("down");
    expect(ab.data?.loss_pct_1m).toBe(100);
    expect(ba.data?.state).toBe("up");
  });

  it("falls back to unknown when no link_state exists for an edge", () => {
    const s = snap({
      agents: [agent("a"), agent("b")],
      peer_assignments: [peer("a", "b")],
    });
    const g = buildMeshGraph(s);
    expect(g.edges[0].data?.state).toBe("unknown");
  });
});
