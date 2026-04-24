from enum import StrEnum


class AgentState(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    STALE = "stale"
    REVOKED = "revoked"


class CommandType(StrEnum):
    UPDATE_PEERS = "update_peers"
    TCP_PROBE = "tcp_probe"
    DNS_PROBE = "dns_probe"
    HTTP_PROBE = "http_probe"
    IPERF3_SERVER_START = "iperf3_server_start"
    IPERF3_SERVER_STOP = "iperf3_server_stop"
    IPERF3_CLIENT = "iperf3_client"
    DHCP_RENEW = "dhcp_renew"
    SELF_UPGRADE = "self_upgrade"
    RESTART = "restart"
    RELOAD_CONFIG = "reload_config"


class CommandStatus(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    DONE = "done"
    FAILED = "failed"
    EXPIRED = "expired"


class TestType(StrEnum):
    __test__ = False
    TCP_PROBE = "tcp_probe"
    DNS_PROBE = "dns_probe"
    HTTP_PROBE = "http_probe"
    IPERF3_PAIR = "iperf3_pair"


class TestState(StrEnum):
    __test__ = False
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class IperfSessionState(StrEnum):
    REQUESTED = "requested"
    SERVER_STARTING = "server_starting"
    CLIENT_RUNNING = "client_running"
    COLLECTING = "collecting"
    DONE = "done"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class LinkState(StrEnum):
    UNKNOWN = "unknown"
    UP = "up"
    DEGRADED = "degraded"
    DOWN = "down"


class WebhookDeliveryState(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    DEAD = "dead"


class InterfaceRole(StrEnum):
    """Admin-classified role for an agent's network interface.

    - `test`: peers ping this interface's current IP.
    - `management`: informational only — the agent uses this to reach the server.
    - `ignored`: interface exists but nobody cares about it.
    - `unknown`: freshly reported, not yet classified by admin.
    - `monitor`: wireless airspace monitor — agent runs `iw scan` on this iface
      and reports visible BSSIDs. Agents with any monitor-role interface are
      excluded from the peer-mesh (no pings in or out).
    """

    TEST = "test"
    MANAGEMENT = "management"
    IGNORED = "ignored"
    UNKNOWN = "unknown"
    MONITOR = "monitor"


TERMINAL_IPERF_STATES = frozenset(
    {
        IperfSessionState.DONE,
        IperfSessionState.FAILED,
        IperfSessionState.TIMEOUT,
        IperfSessionState.CANCELLED,
    }
)

TERMINAL_TEST_STATES = frozenset(
    {
        TestState.SUCCEEDED,
        TestState.FAILED,
        TestState.TIMEOUT,
        TestState.CANCELLED,
    }
)
