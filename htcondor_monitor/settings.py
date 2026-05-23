"""
Central configuration.  All values can be overridden via environment variables
or a .env file at the project root.

  HTCONDOR_OPENSEARCH_HOST=https://my-cluster:9200
  HTCONDOR_OPENSEARCH_INDEX_PREFIX=htcondor-jobs
  HTCONDOR_ANTHROPIC_MODEL=claude-sonnet-4-20250514
  ...
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal
import warnings

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "htcondor_monitor"


_ENV_FILE = _config_dir() / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HTCONDOR_",
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
    )
    @model_validator(mode="after")
    def warn_if_no_env_file(self) -> "Settings":
        if not _ENV_FILE.exists():
            warnings.warn(
                f"{_ENV_FILE} not found — running with default settings.\n"
                "To use custom settings, copy examples/.env.example to "
                "~/.config/htcondor_monitor/.env and configure it before use.\n"
                "Be sure to use `chmod 600 .env` to ensure that file is "
                "owner-only readable",
                stacklevel=2,
            )
        else:
            mode = _ENV_FILE.stat().st_mode & 0o777
            if mode & 0o077:  # any group or world bits set
                warnings.warn(
                    f"{_ENV_FILE} is readable by group or world (mode {oct(mode)}). "
                    f"Run: chmod 600 {_ENV_FILE}",
                    stacklevel=2,
                )
        return self

    # ── OpenSearch ──────────────────────────────────────────────────────────
    opensearch_host: str = "https://localhost"
    opensearch_port: int = 9200
    opensearch_index_prefix: str = "htcondor-jobs"
    opensearch_username: str | None = None
    opensearch_password: str | None = None
    opensearch_ca_cert: str | None = None
    opensearch_timeout: int = 30
    # Maximum docs to retrieve per query (guards against huge scrolls)
    opensearch_max_results: int = 10_000
    opensearch_use_keyword_suffix: bool = False

    # ── ClassAd field mapping ───────────────────────────────────────────────
    # Override these if your ingest pipeline uses different field names.
    field_user: str = "Owner"
    field_cluster_id: str = "ClusterId"
    field_proc_id: str = "ProcId"
    field_submit_time: str = "QDate"          # epoch seconds
    field_start_time: str = "JobStartDate"
    field_completion_time: str = "CompletionDate"
    field_cpu_user: str = "RemoteUserCpu"     # seconds
    field_cpu_sys: str = "RemoteSysCpu"
    field_wall_time: str = "RemoteWallClockTime"
    field_memory_request: str = "RequestMemory"   # MB
    field_memory_usage: str = "ResidentSetSize"   # KB
    field_disk_request: str = "RequestDisk"       # KB
    field_disk_usage: str = "DiskUsage"           # KB
    field_cpus_request: str = "RequestCpus"
    field_gpus_request: str = "RequestGpus"
    field_hold_reason: str = "HoldReason"
    field_hold_reason_code: str = "HoldReasonCode"
    field_exit_code: str = "ExitCode"
    field_job_status: str = "JobStatus"
    field_num_job_starts: str = "NumJobStarts"
    field_num_shadow_exceptions: str = "NumShadowExceptions"
    field_last_remote_host: str = "LastRemoteHost"
    field_bytes_sent: str = "BytesSent"
    field_bytes_received: str = "BytesReceived"

    # ── LLM / smolagents ───────────────────────────────────────────────────
    anthropic_api_key: str | None = None   # falls back to ANTHROPIC_API_KEY env var
    anthropic_model: str = "claude-sonnet-4-20250514"
    anthropic_base_url: str | None = None
    agent_max_steps: int = 20
    agent_verbosity: int = 1   # 0=quiet, 1=normal, 2=debug

    # ── Thresholds used in prompts ─────────────────────────────────────────
    cpu_efficiency_warn_pct: float = 30.0
    memory_overrequest_ratio: float = 2.0
    memory_exceeded_pct: float = 20.0
    eviction_warn_count: int = 2
    node_failure_rate_pct: float = 15.0
    shadow_exception_warn: int = 1
    long_job_multiplier: float = 3.0
    long_job_fallback_hours: float = 48.0
    anomaly_stddev_threshold: float = 2.0
    gpu_idle_threshold_pct: float = 10.0

    # ── State / persistence ────────────────────────────────────────────────
    state_dir: Path = Path("./monitor_state")
    # How many prior run summaries to retain and include as context
    state_history_depth: int = 5

    # ── Reporting ─────────────────────────────────────────────────────────
    report_output_dir: Path = Path("./reports")
    # Comma-separated email addresses, or empty to skip email
    report_email_to: str = ""
    report_email_from: str = "htcondor-monitor@localhost"
    smtp_host: str = "localhost"
    smtp_port: int = 25

    # ── Cadence labels (informational, used in filenames / subject lines) ──
    cadence: Literal["daily", "weekly", "monthly", "adhoc"] = "daily"


# Module-level singleton — import this everywhere
settings = Settings()
