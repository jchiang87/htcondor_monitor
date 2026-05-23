"""Tests for Settings defaults, field names, and threshold values.

Strategy for env isolation:
- Default value tests read `Settings.model_fields["field"].default` directly
  from the class — no instantiation, so neither the .env file nor env vars
  can interfere.
- Override / env-var tests instantiate Settings with `_env_file=None` to skip
  ~/.config/htcondor_monitor/.env; init kwargs take highest priority so they
  always win over any ambient HTCONDOR_* env vars.
"""

from __future__ import annotations

import warnings

import pytest

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from htcondor_monitor.settings import Settings


def _default(field: str):
    """Return the declared default for a Settings field without instantiating."""
    return Settings.model_fields[field].default


def make_settings(**kwargs) -> Settings:
    """Instantiate Settings with the .env file disabled."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return Settings(_env_file=None, **kwargs)


# ── OpenSearch defaults ────────────────────────────────────────────────────────

def test_default_opensearch_host():
    assert _default("opensearch_host") == "https://localhost"


def test_default_opensearch_port():
    assert _default("opensearch_port") == 9200


def test_default_opensearch_index_prefix():
    assert _default("opensearch_index_prefix") == "htcondor-jobs"


# ── ClassAd field name defaults ────────────────────────────────────────────────

def test_default_field_user():
    assert _default("field_user") == "Owner"


def test_default_field_cluster_id():
    assert _default("field_cluster_id") == "ClusterId"


def test_default_field_proc_id():
    assert _default("field_proc_id") == "ProcId"


def test_default_field_job_status():
    assert _default("field_job_status") == "JobStatus"


def test_default_field_hold_reason():
    assert _default("field_hold_reason") == "HoldReason"


def test_default_field_cpu_user():
    assert _default("field_cpu_user") == "RemoteUserCpu"


def test_default_field_wall_time():
    assert _default("field_wall_time") == "RemoteWallClockTime"


def test_default_field_memory_request():
    assert _default("field_memory_request") == "RequestMemory"


def test_default_field_memory_usage():
    assert _default("field_memory_usage") == "ResidentSetSize"


# ── Threshold defaults ─────────────────────────────────────────────────────────

def test_default_cpu_efficiency_warn_pct():
    assert _default("cpu_efficiency_warn_pct") == 30.0


def test_default_memory_overrequest_ratio():
    assert _default("memory_overrequest_ratio") == 2.0


def test_default_node_failure_rate_pct():
    assert _default("node_failure_rate_pct") == 15.0


def test_default_anomaly_stddev_threshold():
    assert _default("anomaly_stddev_threshold") == 2.0


def test_default_gpu_idle_threshold_pct():
    assert _default("gpu_idle_threshold_pct") == 10.0


def test_default_long_job_fallback_hours():
    assert _default("long_job_fallback_hours") == 48.0


# ── State / LLM defaults ──────────────────────────────────────────────────────

def test_default_state_history_depth():
    assert _default("state_history_depth") == 5


def test_default_anthropic_model():
    assert _default("anthropic_model") == "claude-sonnet-4-20250514"


def test_default_agent_max_steps():
    assert _default("agent_max_steps") == 20


# ── Override via kwargs (.env skipped; init kwargs have highest priority) ──────

def test_custom_field_user_via_kwargs():
    assert make_settings(field_user="User").field_user == "User"


def test_custom_cpu_threshold_via_kwargs():
    assert make_settings(cpu_efficiency_warn_pct=50.0).cpu_efficiency_warn_pct == 50.0


def test_custom_state_history_depth_via_kwargs():
    assert make_settings(state_history_depth=10).state_history_depth == 10


# ── Env var override (.env skipped; only the monkeypatched env var is active) ──

def test_env_prefix_overrides_opensearch_port(monkeypatch):
    monkeypatch.setenv("HTCONDOR_OPENSEARCH_PORT", "9201")
    assert make_settings().opensearch_port == 9201


def test_env_prefix_overrides_field_user(monkeypatch):
    monkeypatch.setenv("HTCONDOR_FIELD_USER", "JobOwner")
    assert make_settings().field_user == "JobOwner"
