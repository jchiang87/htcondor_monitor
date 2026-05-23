"""Tests for PromptBuilder Jinja2 template rendering."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from htcondor_monitor.builder import PromptBuilder, TEMPLATES
from htcondor_monitor.settings import settings
from htcondor_monitor.store import RunRecord, StateStore

EXPECTED_TASKS = {
    "health_check",
    "resource_efficiency",
    "node_health",
    "long_running_jobs",
    "user_behavior_trends",
    "gpu_utilization",
    "anomaly_detection",
}


@pytest.fixture
def builder(tmp_path) -> PromptBuilder:
    """PromptBuilder with an isolated StateStore so tests don't touch the real state dir."""
    store = StateStore(state_dir=tmp_path)
    return PromptBuilder(state_store=store)


@pytest.fixture
def builder_with_history(tmp_path) -> PromptBuilder:
    store = StateStore(state_dir=tmp_path)
    record = RunRecord(
        cadence="daily",
        task_name="health_check",
        run_at=datetime(2026, 5, 19, 8, 0, 0, tzinfo=timezone.utc),
        findings_summary="Prior run found high memory usage for user carol.",
        findings_json={"executive_summary": "Prior run found high memory usage for user carol."},
    )
    store.save(record)
    return PromptBuilder(state_store=store)


# ── Task enumeration ──────────────────────────────────────────────────────────

def test_available_tasks_count(builder):
    assert len(builder.available_tasks) == 7


def test_available_tasks_contains_expected_names(builder):
    assert set(builder.available_tasks) == EXPECTED_TASKS


# ── Error handling ────────────────────────────────────────────────────────────

def test_build_unknown_task_raises_value_error(builder):
    with pytest.raises(ValueError, match="Unknown task"):
        builder.build("nonexistent_task")


# ── Rendering basics ──────────────────────────────────────────────────────────

def test_build_returns_nonempty_string(builder):
    prompt = builder.build("health_check", cadence="daily")
    assert isinstance(prompt, str)
    assert len(prompt) > 100


@pytest.mark.parametrize("task", list(EXPECTED_TASKS))
def test_build_all_tasks_no_crash(builder, task):
    prompt = builder.build(task, cadence="daily")
    assert isinstance(prompt, str)
    assert len(prompt) > 50


# ── Field name injection (uses real settings defaults) ────────────────────────

def test_build_renders_field_user_in_health_check(builder):
    prompt = builder.build("health_check", cadence="daily")
    assert settings.field_user in prompt


def test_build_renders_field_job_status(builder):
    prompt = builder.build("health_check", cadence="daily")
    assert settings.field_job_status in prompt


def test_build_renders_field_hold_reason(builder):
    prompt = builder.build("health_check", cadence="daily")
    assert settings.field_hold_reason in prompt


# ── Threshold injection ───────────────────────────────────────────────────────

def test_build_renders_cpu_efficiency_threshold(builder):
    prompt = builder.build("health_check", cadence="daily")
    assert str(settings.cpu_efficiency_warn_pct) in prompt


def test_build_renders_memory_overrequest_ratio(builder):
    prompt = builder.build("resource_efficiency", cadence="weekly")
    assert str(settings.memory_overrequest_ratio) in prompt


def test_build_renders_node_failure_rate(builder):
    prompt = builder.build("node_health", cadence="weekly")
    assert str(settings.node_failure_rate_pct) in prompt


# ── Prior context injection ───────────────────────────────────────────────────

def test_build_no_prior_history_contains_no_prior_runs(builder):
    prompt = builder.build("health_check", cadence="daily")
    assert "No prior runs" in prompt


def test_build_includes_prior_run_summary(builder_with_history):
    prompt = builder_with_history.build("health_check", cadence="daily")
    assert "Prior run found high memory usage for user carol." in prompt


# ── Period label injection ────────────────────────────────────────────────────

def test_build_period_label_daily(builder):
    prompt = builder.build("user_behavior_trends", cadence="daily")
    assert "24 hours" in prompt


def test_build_period_label_weekly(builder):
    prompt = builder.build("user_behavior_trends", cadence="weekly")
    assert "7 days" in prompt


def test_build_period_label_monthly(builder):
    prompt = builder.build("user_behavior_trends", cadence="monthly")
    assert "30 days" in prompt


# ── Output format section presence ───────────────────────────────────────────

def test_build_health_check_has_json_output_section(builder):
    prompt = builder.build("health_check", cadence="daily")
    assert "executive_summary" in prompt
    assert "flagged_users" in prompt


def test_build_resource_efficiency_has_recommendations(builder):
    prompt = builder.build("resource_efficiency", cadence="weekly")
    assert "recommendations" in prompt
