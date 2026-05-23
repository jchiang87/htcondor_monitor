"""Tests for htcondor_monitor/orchestrators.py.

The query layer (opensearch_queries) is mocked so no OpenSearch connection is needed.
The real metrics layer runs against the mock query output, testing orchestrator integration.
"""

from __future__ import annotations

import warnings
from unittest.mock import patch, MagicMock

import pytest

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from htcondor_monitor import orchestrators
    from htcondor_monitor.orchestrators import run_orchestrator, ORCHESTRATORS


# ── Shared mock data ───────────────────────────────────────────────────────────

_EMPTY_USER_STATS: list[dict] = []
_EMPTY_NODE_STATS: list[dict] = []
_EMPTY_HOLD_REASONS: list[dict] = []
_EMPTY_FLEET_PERCENTILES: dict = {}
_EMPTY_JOBS: list[dict] = []

_SAMPLE_USER_STATS = [
    {
        "user": "alice",
        "job_count": 50,
        "avg_cpu_user": 100.0,
        "avg_wall_time": 200.0,
        "avg_memory_request": 2.0,       # MB — kept small so ratio doesn't round to 0
        "avg_memory_usage_kb": 1024.0,   # 1 MB → ratio = 0.5, no overrequest flag
        "held_jobs": 2,
        "total_job_starts": 55,
        "shadow_exceptions": 1,
    }
]

_SAMPLE_NODE_STATS = [
    {
        "node": "node01.example.com",
        "total_jobs": 100,
        "failed_jobs": 5,
        "failure_rate_pct": 5.0,
        "shadow_exceptions": 0,
        "total_job_starts": 100,
        "exit_codes": {0: 95, 1: 5},
    }
]

_SAMPLE_HOLD_REASONS = [
    {"hold_reason": "Error reading input", "count": 3, "users": ["alice"]},
]


# ── ORCHESTRATORS registry ─────────────────────────────────────────────────────

def test_orchestrators_registry_has_all_tasks():
    expected = {
        "health_check",
        "resource_efficiency",
        "node_health",
        "anomaly_detection",
        "user_behavior_trends",
        "gpu_utilization",
        "long_running_jobs",
    }
    assert set(ORCHESTRATORS.keys()) == expected


def test_orchestrators_registry_values_are_tuples():
    for name, entry in ORCHESTRATORS.items():
        assert isinstance(entry, tuple), f"{name} should map to a (fn, int) tuple"
        fn, hours = entry
        assert callable(fn)
        assert isinstance(hours, int)


# ── run_orchestrator dispatch ──────────────────────────────────────────────────

def test_run_orchestrator_raises_for_unknown_task():
    with pytest.raises(ValueError, match="Unknown task"):
        run_orchestrator("nonexistent_task")


def test_run_orchestrator_uses_default_hours_when_none():
    with patch.object(orchestrators, "q") as mock_q, \
         patch.object(orchestrators, "m") as mock_m:
        mock_q.fetch_user_aggregations.return_value = []
        mock_q.fetch_node_aggregations.return_value = []
        mock_q.fetch_hold_reasons.return_value = []
        mock_m.find_low_cpu_efficiency.return_value = []
        mock_m.find_memory_exceeded.return_value = []
        mock_m.find_high_hold_rates.return_value = []
        mock_m.find_excessive_evictions.return_value = []
        mock_m.find_unhealthy_nodes.return_value = []
        mock_m.classify_exit_codes.return_value = []

        result = run_orchestrator("health_check", hours_back=None)
        # Default for health_check is 24
        assert result["hours_back"] == 24


def test_run_orchestrator_honors_hours_back_override():
    with patch.object(orchestrators, "q") as mock_q, \
         patch.object(orchestrators, "m") as mock_m:
        mock_q.fetch_user_aggregations.return_value = []
        mock_q.fetch_node_aggregations.return_value = []
        mock_q.fetch_hold_reasons.return_value = []
        mock_m.find_low_cpu_efficiency.return_value = []
        mock_m.find_memory_exceeded.return_value = []
        mock_m.find_high_hold_rates.return_value = []
        mock_m.find_excessive_evictions.return_value = []
        mock_m.find_unhealthy_nodes.return_value = []
        mock_m.classify_exit_codes.return_value = []

        result = run_orchestrator("health_check", hours_back=48)
        assert result["hours_back"] == 48


# ── run_health_check ───────────────────────────────────────────────────────────

@pytest.fixture
def mock_q_basic():
    """Patch the query module with empty returns."""
    with patch.object(orchestrators, "q") as mock_q:
        mock_q.fetch_user_aggregations.return_value = _EMPTY_USER_STATS
        mock_q.fetch_node_aggregations.return_value = _EMPTY_NODE_STATS
        mock_q.fetch_hold_reasons.return_value = _EMPTY_HOLD_REASONS
        mock_q.fetch_fleet_percentiles.return_value = _EMPTY_FLEET_PERCENTILES
        mock_q.fetch_jobs.return_value = _EMPTY_JOBS
        yield mock_q


def test_run_health_check_returns_required_keys(mock_q_basic):
    result = orchestrators.run_health_check(hours_back=24)
    required = {
        "hours_back", "total_users", "total_nodes",
        "low_cpu_efficiency", "memory_exceeded", "high_hold_rates",
        "excessive_evictions", "unhealthy_nodes", "hold_reason_summary",
        "exit_code_analysis", "thresholds",
    }
    assert required.issubset(set(result.keys()))


def test_run_health_check_hours_back_propagated(mock_q_basic):
    result = orchestrators.run_health_check(hours_back=12)
    assert result["hours_back"] == 12


def test_run_health_check_total_users_reflects_query(mock_q_basic):
    mock_q_basic.fetch_user_aggregations.return_value = _SAMPLE_USER_STATS
    result = orchestrators.run_health_check(hours_back=24)
    assert result["total_users"] == 1


def test_run_health_check_total_nodes_reflects_query(mock_q_basic):
    mock_q_basic.fetch_node_aggregations.return_value = _SAMPLE_NODE_STATS
    result = orchestrators.run_health_check(hours_back=24)
    assert result["total_nodes"] == 1


def test_run_health_check_thresholds_dict_present(mock_q_basic):
    result = orchestrators.run_health_check(hours_back=24)
    thresholds = result["thresholds"]
    assert "cpu_efficiency_warn_pct" in thresholds
    assert "memory_exceeded_pct" in thresholds
    assert "eviction_warn_count" in thresholds
    assert "node_failure_rate_pct" in thresholds


# ── run_resource_efficiency ────────────────────────────────────────────────────

def test_run_resource_efficiency_returns_required_keys(mock_q_basic):
    result = orchestrators.run_resource_efficiency(hours_back=168)
    required = {
        "hours_back", "active_user_count",
        "low_cpu_efficiency", "memory_exceeded", "memory_overrequested",
        "excessive_evictions", "wasted_cpu_hours_ranked", "wasted_memory_ranked",
        "per_user_stats", "thresholds",
    }
    assert required.issubset(set(result.keys()))


def test_run_resource_efficiency_filters_to_active_users(mock_q_basic):
    # User with only 3 jobs should be excluded (< 5 minimum)
    low_job_user = {**_SAMPLE_USER_STATS[0], "user": "bob", "job_count": 3}
    mock_q_basic.fetch_user_aggregations.return_value = [
        _SAMPLE_USER_STATS[0],
        low_job_user,
    ]
    result = orchestrators.run_resource_efficiency(hours_back=168)
    # alice has 50 jobs → active; bob has 3 → excluded
    assert result["active_user_count"] == 1


def test_run_resource_efficiency_wasted_lists_capped_at_20(mock_q_basic):
    many_users = [
        {**_SAMPLE_USER_STATS[0], "user": f"user{i}", "job_count": 10}
        for i in range(30)
    ]
    mock_q_basic.fetch_user_aggregations.return_value = many_users
    result = orchestrators.run_resource_efficiency(hours_back=168)
    assert len(result["wasted_cpu_hours_ranked"]) <= 20
    assert len(result["wasted_memory_ranked"]) <= 20


# ── run_node_health ────────────────────────────────────────────────────────────

def test_run_node_health_returns_required_keys(mock_q_basic):
    result = orchestrators.run_node_health(hours_back=168)
    required = {
        "hours_back", "total_nodes", "unhealthy_nodes",
        "exit_code_analysis", "hold_reason_summary", "all_node_stats", "thresholds",
    }
    assert required.issubset(set(result.keys()))


def test_run_node_health_all_node_stats_capped_at_50(mock_q_basic):
    many_nodes = [
        {**_SAMPLE_NODE_STATS[0], "node": f"node{i:02d}"}
        for i in range(60)
    ]
    mock_q_basic.fetch_node_aggregations.return_value = many_nodes
    result = orchestrators.run_node_health(hours_back=168)
    assert len(result["all_node_stats"]) <= 50


def test_run_node_health_total_nodes_reflects_query(mock_q_basic):
    mock_q_basic.fetch_node_aggregations.return_value = _SAMPLE_NODE_STATS * 3
    result = orchestrators.run_node_health(hours_back=168)
    assert result["total_nodes"] == 3


# ── run_anomaly_detection ──────────────────────────────────────────────────────

def test_run_anomaly_detection_returns_required_keys(mock_q_basic):
    result = orchestrators.run_anomaly_detection(hours_back=24)
    required = {
        "hours_back", "total_users", "fleet_percentiles",
        "anomalous_users", "new_users", "fleet_outliers",
        "hold_reason_summary", "excessive_evictions", "thresholds",
    }
    assert required.issubset(set(result.keys()))


def test_run_anomaly_detection_queries_both_periods(mock_q_basic):
    orchestrators.run_anomaly_detection(hours_back=24)
    # Should be called twice: once for current (24h) and once for prior (48h)
    assert mock_q_basic.fetch_user_aggregations.call_count == 2


def test_run_anomaly_detection_total_users_is_current_count(mock_q_basic):
    mock_q_basic.fetch_user_aggregations.side_effect = [
        _SAMPLE_USER_STATS,      # current period
        _SAMPLE_USER_STATS * 2,  # prior period (larger)
    ]
    result = orchestrators.run_anomaly_detection(hours_back=24)
    assert result["total_users"] == 1  # current period count


# ── run_user_behavior_trends ───────────────────────────────────────────────────

def test_run_user_behavior_trends_returns_required_keys(mock_q_basic):
    result = orchestrators.run_user_behavior_trends(hours_back=168)
    required = {
        "hours_back", "current_user_count", "prior_user_count",
        "anomalous_users", "new_users", "low_cpu_efficiency",
        "memory_overrequested", "wasted_cpu_hours_ranked", "thresholds",
    }
    assert required.issubset(set(result.keys()))


def test_run_user_behavior_trends_queries_both_periods(mock_q_basic):
    orchestrators.run_user_behavior_trends(hours_back=168)
    assert mock_q_basic.fetch_user_aggregations.call_count == 2


def test_run_user_behavior_trends_wasted_capped_at_10(mock_q_basic):
    many_users = [
        {**_SAMPLE_USER_STATS[0], "user": f"user{i}", "job_count": 10}
        for i in range(15)
    ]
    mock_q_basic.fetch_user_aggregations.return_value = many_users
    result = orchestrators.run_user_behavior_trends(hours_back=168)
    assert len(result["wasted_cpu_hours_ranked"]) <= 10


# ── run_gpu_utilization ────────────────────────────────────────────────────────

def test_run_gpu_utilization_returns_required_keys(mock_q_basic):
    result = orchestrators.run_gpu_utilization(hours_back=168)
    required = {
        "hours_back", "gpu_user_count",
        "low_cpu_efficiency", "wasted_cpu_hours_ranked",
        "node_stats", "gpu_user_stats", "note", "thresholds",
    }
    assert required.issubset(set(result.keys()))


def test_run_gpu_utilization_filters_to_gpu_users(mock_q_basic):
    gpu_user = {**_SAMPLE_USER_STATS[0], "avg_gpus_requested": 2.0}
    non_gpu_user = {**_SAMPLE_USER_STATS[0], "user": "bob", "avg_gpus_requested": 0.0}
    mock_q_basic.fetch_user_aggregations.return_value = [gpu_user, non_gpu_user]
    result = orchestrators.run_gpu_utilization(hours_back=168)
    assert result["gpu_user_count"] == 1


def test_run_gpu_utilization_includes_note_field(mock_q_basic):
    result = orchestrators.run_gpu_utilization(hours_back=168)
    assert isinstance(result["note"], str)
    assert len(result["note"]) > 0


# ── run_long_running_jobs ──────────────────────────────────────────────────────

def test_run_long_running_jobs_returns_required_keys(mock_q_basic):
    result = orchestrators.run_long_running_jobs(hours_back=168)
    required = {
        "hours_back", "total_long_jobs", "good_use_count", "stalled",
        "needs_review", "thresholds", "note",
    }
    assert required.issubset(set(result.keys()))


def test_run_long_running_jobs_queries_running_and_completed(mock_q_basic):
    orchestrators.run_long_running_jobs(hours_back=168)
    # Should call fetch_jobs twice: status=[2] (running) and status=[4] (completed)
    assert mock_q_basic.fetch_jobs.call_count == 2
    call_kwargs = [call.kwargs for call in mock_q_basic.fetch_jobs.call_args_list]
    statuses = [kw["job_status"] for kw in call_kwargs]
    assert [2] in statuses
    assert [4] in statuses


def test_run_long_running_jobs_count_reflects_query_result(mock_q_basic):
    mock_q_basic.fetch_jobs.side_effect = [
        [{"ClusterId": 1}] * 3,  # running
        [{"ClusterId": 2}] * 7,  # completed
    ]
    result = orchestrators.run_long_running_jobs(hours_back=168)
    assert result["total_long_jobs"] == 10


def test_run_long_running_jobs_thresholds_present(mock_q_basic):
    result = orchestrators.run_long_running_jobs(hours_back=168)
    assert "long_job_multiplier" in result["thresholds"]
    assert "long_job_fallback_hours" in result["thresholds"]
