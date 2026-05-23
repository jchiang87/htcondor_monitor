"""Tests for PromptBuilder Jinja2 template rendering."""

from __future__ import annotations

import warnings
from datetime import datetime, timezone

import pytest

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from htcondor_monitor.builder import PromptBuilder, HYBRID_TEMPLATES
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


def test_hybrid_templates_has_all_expected_tasks():
    assert set(HYBRID_TEMPLATES.keys()) == EXPECTED_TASKS


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


# ── Prompt content ────────────────────────────────────────────────────────────

def test_build_health_check_mentions_analyst(builder):
    prompt = builder.build("health_check", cadence="daily")
    assert "HTCondor cluster monitoring analyst" in prompt


def test_build_health_check_has_json_output_section(builder):
    prompt = builder.build("health_check", cadence="daily")
    assert "executive_summary" in prompt
    assert "flagged_users" in prompt


def test_build_resource_efficiency_has_recommendations(builder):
    prompt = builder.build("resource_efficiency", cadence="weekly")
    assert "recommendations" in prompt


def test_build_node_health_mentions_flagged_nodes(builder):
    prompt = builder.build("node_health", cadence="weekly")
    assert "flagged_nodes" in prompt


def test_build_long_running_jobs_mentions_classification(builder):
    prompt = builder.build("long_running_jobs", cadence="daily")
    assert "GOOD_USE" in prompt or "classification" in prompt


def test_build_anomaly_detection_mentions_anomalous_users(builder):
    prompt = builder.build("anomaly_detection", cadence="daily")
    assert "anomalous_users" in prompt


def test_build_gpu_utilization_mentions_flagged_users(builder):
    prompt = builder.build("gpu_utilization", cadence="weekly")
    assert "flagged_users" in prompt


# ── Findings injection ────────────────────────────────────────────────────────

def test_build_with_findings_dict_serializes_to_json(builder):
    findings = {"hours_back": 24, "total_users": 3, "low_cpu_efficiency": []}
    prompt = builder.build("health_check", findings=findings, cadence="daily")
    assert '"hours_back": 24' in prompt
    assert '"total_users": 3' in prompt


def test_build_with_empty_findings_renders_without_crash(builder):
    prompt = builder.build("health_check", findings={}, cadence="daily")
    assert isinstance(prompt, str)
    assert len(prompt) > 50


def test_build_long_running_jobs_with_findings_shows_threshold(builder):
    findings = {"thresholds": {"long_job_fallback_hours": 72.0}}
    prompt = builder.build("long_running_jobs", findings=findings, cadence="daily")
    assert "72.0" in prompt


def test_build_long_running_jobs_empty_findings_falls_back_to_settings(builder):
    prompt = builder.build("long_running_jobs", findings={}, cadence="daily")
    assert str(settings.long_job_fallback_hours) in prompt


# ── Cadence injection ─────────────────────────────────────────────────────────

def test_build_cadence_weekly_in_user_behavior_trends(builder):
    prompt = builder.build("user_behavior_trends", cadence="weekly")
    assert "weekly" in prompt


def test_build_cadence_daily_in_user_behavior_trends(builder):
    prompt = builder.build("user_behavior_trends", cadence="daily")
    assert "daily" in prompt


# ── Prior context injection ───────────────────────────────────────────────────

def test_build_no_prior_history_contains_no_prior_runs(builder):
    prompt = builder.build("health_check", cadence="daily")
    assert "No prior runs" in prompt


def test_build_includes_prior_run_summary(builder_with_history):
    prompt = builder_with_history.build("health_check", cadence="daily")
    assert "Prior run found high memory usage for user carol." in prompt
