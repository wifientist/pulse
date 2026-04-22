from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PULSE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    admin_token: str = Field(description="Bearer token required for all /v1/admin/* routes.")

    db_path: str = "/data/pulse.sqlite"
    log_level: str = "INFO"
    bind_host: str = "0.0.0.0"
    bind_port: int = 8080

    default_poll_interval_s: int = 5
    default_ping_interval_s: int = 5

    raw_retention_hours: int = 48
    minute_retention_days: int = 14

    iperf_port_min: int = 42000
    iperf_port_max: int = 42099

    down_loss_pct: float = 80.0
    degraded_loss_pct: float = 20.0
    degraded_rtt_p95_ms: float = 500.0
    min_dwell_s: int = 60
    recovery_window_s: int = 120

    stale_agent_factor: float = 3.0
    """Mark an agent stale if last_poll_at is older than this × poll_interval_s."""

    @property
    def db_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path}"


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
