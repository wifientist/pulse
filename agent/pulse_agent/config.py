from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PULSE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    server_url: str = Field(description="Base URL of the Pulse server, e.g. https://pulse.lan")
    enrollment_token: str | None = Field(
        default=None,
        description="Pre-shared token used at first enrollment. Not needed after enrollment.",
    )
    token_file: str = Field(
        default="/var/lib/pulse/agent.token",
        description="Where the per-agent bearer token is persisted.",
    )
    hostname: str | None = Field(
        default=None,
        description="Override the auto-detected hostname. Leave unset to use OS hostname.",
    )
    primary_iface: str | None = Field(
        default=None,
        description="Name of the interface whose IP should be reported as primary. "
        "Useful in containers with multiple networks. Default: auto (first non-loopback).",
    )
    reported_ip: str | None = Field(
        default=None,
        description="Override the auto-detected primary IP. Most direct control when "
        "running in containers with multiple networks.",
    )
    log_level: str = "INFO"
    verify_tls: bool = True


def load_agent_settings() -> AgentSettings:
    return AgentSettings()  # type: ignore[call-arg]
