"""Single source of truth for Pulse versions.

`AGENT_VERSION` is what a freshly-rolled agent source tarball reports to the server,
and what the server advertises as its "latest" agent version for self-upgrade checks.
Bump when you ship a meaningful agent change (new probe types, protocol contract
field, etc). The server and UI source this too so all three stay in sync.

`PROTOCOL_VERSION` is the wire-contract version agents send with every poll so the
server can reject mismatched clients if we ever break the contract.
"""

AGENT_VERSION = "0.2.0"
PROTOCOL_VERSION = "1"
