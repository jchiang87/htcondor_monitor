"""Unit tests for htcondor_monitor/tools/metrics.py — pure Python, no OpenSearch."""

from __future__ import annotations

import warnings

import pytest

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from htcondor_monitor.tools import metrics as m
    from htcondor_monitor.settings import settings


# ── Fixture helpers ────────────────────────────────────────────────────────────

def _user(user: str = "alice", job_count: int = 10, **kwargs) -> dict:
    row = {
        "user": user,
        "job_count": job_count,
        "avg_cpu_user": 0.0,
        "avg_wall_time": 0.0,
        "avg_memory_request": 0.0,
        "avg_memory_usage_kb": 0.0,
        "held_jobs": 0,
        "total_job_starts": job_count,
        "shadow_exceptions": 0,
    }
    row.update(kwargs)
    return row


def _node(
    node: str = "node01.example.com",
    total_jobs: int = 100,
    failed_jobs: int = 0,
    **kwargs,
) -> dict:
    failure_rate = round(100.0 * failed_jobs / total_jobs, 1) if total_jobs else 0.0
    row = {
        "node": node,
        "total_jobs": total_jobs,
        "failed_jobs": failed_jobs,
        "failure_rate_pct": failure_rate,
        "shadow_exceptions": 0,
        "total_job_starts": total_jobs,
        "exit_codes": {},
    }
    row.update(kwargs)
    return row


# ── cpu_efficiency ─────────────────────────────────────────────────────────────

def test_cpu_efficiency_basic():
    assert m.cpu_efficiency(50.0, 100.0) == 50.0


def test_cpu_efficiency_perfect():
    assert m.cpu_efficiency(100.0, 100.0) == 100.0


def test_cpu_efficiency_rounds_to_one_decimal():
    assert m.cpu_efficiency(1.0, 3.0) == 33.3


def test_cpu_efficiency_zero_cpu_returns_none():
    assert m.cpu_efficiency(0, 100.0) is None


def test_cpu_efficiency_zero_wall_returns_none():
    assert m.cpu_efficiency(50.0, 0) is None


def test_cpu_efficiency_none_cpu_returns_none():
    assert m.cpu_efficiency(None, 100.0) is None


def test_cpu_efficiency_none_wall_returns_none():
    assert m.cpu_efficiency(50.0, None) is None


# ── find_low_cpu_efficiency ────────────────────────────────────────────────────

def test_find_low_cpu_efficiency_flags_low_user():
    # 5 / 100 = 5%, well below default 30% threshold
    user = _user("alice", avg_cpu_user=5.0, avg_wall_time=100.0)
    results = m.find_low_cpu_efficiency([user])
    assert len(results) == 1
    assert results[0]["user"] == "alice"


def test_find_low_cpu_efficiency_no_flag_for_high_efficiency():
    # 90 / 100 = 90%, above threshold
    user = _user("bob", avg_cpu_user=90.0, avg_wall_time=100.0)
    assert m.find_low_cpu_efficiency([user]) == []


def test_find_low_cpu_efficiency_sorted_ascending():
    users = [
        _user("carol", avg_cpu_user=20.0, avg_wall_time=100.0),
        _user("alice", avg_cpu_user=5.0, avg_wall_time=100.0),
    ]
    results = m.find_low_cpu_efficiency(users)
    assert results[0]["user"] == "alice"
    assert results[1]["user"] == "carol"


def test_find_low_cpu_efficiency_skips_missing_data():
    user = _user("alice", avg_cpu_user=None, avg_wall_time=100.0)
    assert m.find_low_cpu_efficiency([user]) == []


def test_find_low_cpu_efficiency_includes_threshold_in_finding():
    user = _user("alice", avg_cpu_user=5.0, avg_wall_time=100.0)
    result = m.find_low_cpu_efficiency([user])[0]
    assert result["threshold_pct"] == settings.cpu_efficiency_warn_pct


def test_find_low_cpu_efficiency_empty_input():
    assert m.find_low_cpu_efficiency([]) == []


# ── memory_usage_mb ────────────────────────────────────────────────────────────

def test_memory_usage_mb_converts_kb_to_mb():
    assert m.memory_usage_mb(1024.0) == 1.0


def test_memory_usage_mb_rounds_to_one_decimal():
    assert m.memory_usage_mb(1536.0) == 1.5


def test_memory_usage_mb_none_returns_none():
    assert m.memory_usage_mb(None) is None


def test_memory_usage_mb_zero_returns_none():
    assert m.memory_usage_mb(0) is None


# ── memory_overrequest_ratio ───────────────────────────────────────────────────

def test_memory_overrequest_ratio_under_request():
    # 512 KB used / 1 MB requested = 0.5
    assert m.memory_overrequest_ratio(512.0, 1.0) == 0.5


def test_memory_overrequest_ratio_over_request():
    # 2048 KB used / 1 MB requested = 2.0
    assert m.memory_overrequest_ratio(2048.0, 1.0) == 2.0


def test_memory_overrequest_ratio_zero_request_returns_none():
    assert m.memory_overrequest_ratio(512.0, 0) is None


def test_memory_overrequest_ratio_none_usage_returns_none():
    assert m.memory_overrequest_ratio(None, 1.0) is None


def test_memory_overrequest_ratio_none_request_returns_none():
    assert m.memory_overrequest_ratio(512.0, None) is None


# ── find_memory_exceeded ───────────────────────────────────────────────────────

def test_find_memory_exceeded_flags_user_over_threshold():
    # 2000 KB / 1 MB request → ratio ≈ 1.95, threshold = 1.0 + 20%/100 = 1.20 → flagged
    user = _user("alice", avg_memory_usage_kb=2000.0, avg_memory_request=1.0)
    results = m.find_memory_exceeded([user])
    assert len(results) == 1
    assert results[0]["user"] == "alice"


def test_find_memory_exceeded_no_flag_within_threshold():
    # 1100 KB ≈ 1.07 MB, request = 1 MB → ratio ≈ 1.07, below 1.20 threshold
    user = _user("alice", avg_memory_usage_kb=1100.0, avg_memory_request=1.0)
    assert m.find_memory_exceeded([user]) == []


def test_find_memory_exceeded_sorted_descending():
    u1 = _user("alice", avg_memory_usage_kb=3000.0, avg_memory_request=1.0)  # ~2.93
    u2 = _user("bob", avg_memory_usage_kb=2500.0, avg_memory_request=1.0)    # ~2.44
    results = m.find_memory_exceeded([u2, u1])
    assert results[0]["user"] == "alice"


def test_find_memory_exceeded_includes_exceeded_by_pct():
    user = _user("alice", avg_memory_usage_kb=2000.0, avg_memory_request=1.0)
    result = m.find_memory_exceeded([user])[0]
    assert "exceeded_by_pct" in result
    assert result["exceeded_by_pct"] > 0


def test_find_memory_exceeded_missing_data_skipped():
    user = _user("alice", avg_memory_usage_kb=None, avg_memory_request=1.0)
    assert m.find_memory_exceeded([user]) == []


# ── find_memory_overrequested ──────────────────────────────────────────────────

def test_find_memory_overrequested_flags_large_overrequest():
    # 400 KB ≈ 0.39 MB, 1 MB requested → ratio = round(0.39, 2) = 0.39, 1/0.39 ≈ 2.56 > 2.0
    user = _user("alice", avg_memory_usage_kb=400.0, avg_memory_request=1.0)
    results = m.find_memory_overrequested([user])
    assert len(results) == 1
    assert results[0]["user"] == "alice"


def test_find_memory_overrequested_no_flag_appropriate_request():
    # 600 KB ≈ 0.59 MB, 1 MB requested → ratio = 0.59, 1/0.59 ≈ 1.69 < 2.0
    user = _user("alice", avg_memory_usage_kb=600.0, avg_memory_request=1.0)
    assert m.find_memory_overrequested([user]) == []


def test_find_memory_overrequested_includes_suggested_request_when_p90_available():
    user = _user(
        "alice",
        avg_memory_usage_kb=400.0,
        avg_memory_request=1.0,
        avg_memory_usage_kb_p90=500.0,
    )
    result = m.find_memory_overrequested([user])[0]
    assert result["suggested_request_mb"] is not None


def test_find_memory_overrequested_suggested_request_none_without_p90():
    user = _user("alice", avg_memory_usage_kb=400.0, avg_memory_request=1.0)
    result = m.find_memory_overrequested([user])[0]
    assert result["suggested_request_mb"] is None


def test_find_memory_overrequested_sorted_by_overrequest_ratio_descending():
    # alice: 100 KB / 1 MB → ratio=0.10, overrequest=10.0
    # bob:   200 KB / 1 MB → ratio=0.20, overrequest=5.0
    u1 = _user("alice", avg_memory_usage_kb=100.0, avg_memory_request=1.0)
    u2 = _user("bob", avg_memory_usage_kb=200.0, avg_memory_request=1.0)
    results = m.find_memory_overrequested([u2, u1])
    assert results[0]["user"] == "alice"


# ── find_high_hold_rates ───────────────────────────────────────────────────────

def test_find_high_hold_rates_flags_user_above_threshold():
    # 5 held / 10 total = 50% > 30% threshold
    user = _user("alice", job_count=10, held_jobs=5)
    results = m.find_high_hold_rates([user])
    assert len(results) == 1
    assert results[0]["user"] == "alice"


def test_find_high_hold_rates_skips_small_job_counts():
    # Only 3 jobs, below minimum of 5
    user = _user("alice", job_count=3, held_jobs=2)
    assert m.find_high_hold_rates([user]) == []


def test_find_high_hold_rates_no_flag_below_threshold():
    # 1 held / 100 total = 1%
    user = _user("alice", job_count=100, held_jobs=1)
    assert m.find_high_hold_rates([user]) == []


def test_find_high_hold_rates_includes_hold_rate_pct():
    user = _user("alice", job_count=10, held_jobs=5)
    result = m.find_high_hold_rates([user])[0]
    assert "hold_rate_pct" in result
    assert result["hold_rate_pct"] == 50.0


# ── find_excessive_evictions ───────────────────────────────────────────────────

def test_find_excessive_evictions_flags_user():
    # 10 jobs, 50 starts → 40 evictions, avg 4.0 > eviction_warn_count(2)
    user = _user("alice", job_count=10, total_job_starts=50)
    results = m.find_excessive_evictions([user])
    assert len(results) == 1
    assert results[0]["user"] == "alice"


def test_find_excessive_evictions_no_flag_below_threshold():
    # 10 jobs, 11 starts → avg 0.1 < 2
    user = _user("alice", job_count=10, total_job_starts=11)
    assert m.find_excessive_evictions([user]) == []


def test_find_excessive_evictions_sorted_by_avg_descending():
    u1 = _user("alice", job_count=10, total_job_starts=50)  # avg = 4.0
    u2 = _user("bob", job_count=10, total_job_starts=40)    # avg = 3.0
    results = m.find_excessive_evictions([u2, u1])
    assert results[0]["user"] == "alice"


def test_find_excessive_evictions_includes_avg_evictions_per_job():
    user = _user("alice", job_count=10, total_job_starts=50)
    result = m.find_excessive_evictions([user])[0]
    assert result["avg_evictions_per_job"] == 4.0


def test_find_excessive_evictions_empty_input():
    assert m.find_excessive_evictions([]) == []


# ── find_unhealthy_nodes ───────────────────────────────────────────────────────

def test_find_unhealthy_nodes_flags_above_threshold():
    node = _node("node01", total_jobs=100, failed_jobs=20)  # 20% > 15%
    results = m.find_unhealthy_nodes([node])
    assert len(results) == 1
    assert results[0]["node"] == "node01"


def test_find_unhealthy_nodes_no_flag_below_threshold():
    node = _node("node01", total_jobs=100, failed_jobs=10)  # 10% < 15%
    assert m.find_unhealthy_nodes([node]) == []


def test_find_unhealthy_nodes_recommendation_drain_at_double_threshold():
    # 40% > 2 * 15% = 30%
    node = _node("node01", total_jobs=100, failed_jobs=40)
    result = m.find_unhealthy_nodes([node])[0]
    assert "drain" in result["recommendation"]


def test_find_unhealthy_nodes_recommendation_monitor_near_threshold():
    # 20%, between 15% and 30%
    node = _node("node01", total_jobs=100, failed_jobs=20)
    result = m.find_unhealthy_nodes([node])[0]
    assert "monitor" in result["recommendation"]


def test_find_unhealthy_nodes_includes_shadow_exceptions():
    node = _node("node01", total_jobs=100, failed_jobs=20, shadow_exceptions=5)
    result = m.find_unhealthy_nodes([node])[0]
    assert result["shadow_exceptions"] == 5


def test_find_unhealthy_nodes_empty_input():
    assert m.find_unhealthy_nodes([]) == []


# ── classify_exit_codes ────────────────────────────────────────────────────────

def test_classify_exit_codes_skips_nodes_with_only_zero_exit():
    node = _node("node01", exit_codes={0: 100})
    assert m.classify_exit_codes([node]) == []


def test_classify_exit_codes_infrastructure_for_high_failure_rate():
    node = _node("node01", total_jobs=100, failed_jobs=20, exit_codes={1: 20})
    results = m.classify_exit_codes([node])
    assert len(results) == 1
    assert results[0]["likely_cause"] == "infrastructure"


def test_classify_exit_codes_application_for_low_failure_rate():
    # 5% < 15% threshold → application
    node = _node("node01", total_jobs=1000, failed_jobs=5,
                 exit_codes={1: 5}, failure_rate_pct=0.5)
    results = m.classify_exit_codes([node])
    assert results[0]["likely_cause"] == "application"


def test_classify_exit_codes_skips_empty_exit_codes():
    node = _node("node01", exit_codes={})
    assert m.classify_exit_codes([node]) == []


def test_classify_exit_codes_excludes_zero_key():
    # node with exit code 0 and exit code 1 — only code 1 should appear
    node = _node("node01", total_jobs=100, failed_jobs=20,
                 exit_codes={0: 80, 1: 20})
    result = m.classify_exit_codes([node])[0]
    assert 0 not in result["exit_codes"]
    assert 1 in result["exit_codes"]


# ── find_anomalous_users ───────────────────────────────────────────────────────

def test_find_anomalous_users_flags_burst_submission():
    prior = [_user("alice", job_count=10)]
    current = [_user("alice", job_count=40)]  # 4× > 3.0 threshold
    results = m.find_anomalous_users(current, prior)
    assert len(results) == 1
    assert results[0]["user"] == "alice"
    metrics = [a["metric"] for a in results[0]["anomalies"]]
    assert "submission_rate" in metrics


def test_find_anomalous_users_no_flag_for_normal_rate():
    prior = [_user("alice", job_count=10)]
    current = [_user("alice", job_count=12)]  # 1.2×, normal
    assert m.find_anomalous_users(current, prior) == []


def test_find_anomalous_users_flags_cpu_efficiency_drop():
    # prior: 80%, current: 5% → drop 75ppt > 2.0 * 10 = 20ppt threshold
    prior = [_user("alice", avg_cpu_user=80.0, avg_wall_time=100.0)]
    current = [_user("alice", avg_cpu_user=5.0, avg_wall_time=100.0)]
    results = m.find_anomalous_users(current, prior)
    assert len(results) == 1
    assert any(a["metric"] == "cpu_efficiency" for a in results[0]["anomalies"])


def test_find_anomalous_users_skips_new_users():
    # Users not in prior are handled by find_new_users, not here
    prior = []
    current = [_user("alice", job_count=100)]
    assert m.find_anomalous_users(current, prior) == []


def test_find_anomalous_users_flags_hold_rate_increase():
    # prior: 2% hold, current: 10% (> 5% and > 2× prior)
    prior = [_user("alice", job_count=100, held_jobs=2)]
    current = [_user("alice", job_count=100, held_jobs=10)]
    results = m.find_anomalous_users(current, prior)
    assert any(a["metric"] == "hold_rate" for r in results for a in r["anomalies"])


def test_find_anomalous_users_empty_inputs():
    assert m.find_anomalous_users([], []) == []


# ── find_new_users ─────────────────────────────────────────────────────────────

def test_find_new_users_identifies_users_not_in_prior():
    prior = [_user("alice")]
    current = [_user("alice"), _user("bob")]
    assert m.find_new_users(current, prior) == ["bob"]


def test_find_new_users_empty_when_all_known():
    prior = [_user("alice"), _user("bob")]
    current = [_user("alice"), _user("bob")]
    assert m.find_new_users(current, prior) == []


def test_find_new_users_all_new_when_no_prior():
    current = [_user("alice"), _user("bob")]
    assert set(m.find_new_users(current, [])) == {"alice", "bob"}


def test_find_new_users_empty_current():
    prior = [_user("alice")]
    assert m.find_new_users([], prior) == []


# ── find_fleet_outliers ────────────────────────────────────────────────────────

def test_find_fleet_outliers_flags_high_wall_time():
    user = _user("alice", avg_wall_time=10000.0)
    fleet = {"wall_time_p99": 5000.0}
    results = m.find_fleet_outliers([user], fleet)
    assert len(results) == 1
    assert results[0]["user"] == "alice"


def test_find_fleet_outliers_no_flag_below_p99():
    user = _user("alice", avg_wall_time=1000.0)
    fleet = {"wall_time_p99": 5000.0}
    assert m.find_fleet_outliers([user], fleet) == []


def test_find_fleet_outliers_flags_high_memory():
    user = _user("alice", avg_memory_usage_kb=200000.0)
    fleet = {"memory_kb_p99": 100000.0}
    results = m.find_fleet_outliers([user], fleet)
    assert len(results) == 1


def test_find_fleet_outliers_includes_flag_descriptions():
    user = _user("alice", avg_wall_time=10000.0)
    fleet = {"wall_time_p99": 5000.0}
    result = m.find_fleet_outliers([user], fleet)[0]
    assert "flags" in result
    assert len(result["flags"]) > 0


def test_find_fleet_outliers_empty_fleet_percentiles():
    user = _user("alice", avg_wall_time=99999.0, avg_memory_usage_kb=99999.0)
    # No p99 values → nothing to compare → no flags
    assert m.find_fleet_outliers([user], {}) == []


# ── rank_by_wasted_cpu_hours ───────────────────────────────────────────────────

def test_rank_by_wasted_cpu_hours_sorted_descending():
    users = [
        _user("alice", job_count=10, avg_wall_time=3600.0, avg_cpu_user=0.0),   # 10h
        _user("bob", job_count=100, avg_wall_time=3600.0, avg_cpu_user=0.0),    # 100h
    ]
    results = m.rank_by_wasted_cpu_hours(users)
    assert results[0]["user"] == "bob"
    assert results[1]["user"] == "alice"


def test_rank_by_wasted_cpu_hours_zero_waste_excluded():
    # cpu_user == wall_time → no waste
    user = _user("alice", job_count=10, avg_wall_time=100.0, avg_cpu_user=100.0)
    assert m.rank_by_wasted_cpu_hours([user]) == []


def test_rank_by_wasted_cpu_hours_includes_efficiency():
    user = _user("alice", job_count=10, avg_wall_time=100.0, avg_cpu_user=50.0)
    result = m.rank_by_wasted_cpu_hours([user])[0]
    assert result["avg_cpu_eff_pct"] == 50.0


def test_rank_by_wasted_cpu_hours_empty_input():
    assert m.rank_by_wasted_cpu_hours([]) == []


# ── rank_by_wasted_memory_gb_hours ────────────────────────────────────────────

def test_rank_by_wasted_memory_gb_hours_sorted_descending():
    users = [
        _user("alice", job_count=1, avg_memory_request=1024.0,
              avg_memory_usage_kb=0.0, avg_wall_time=3600.0),
        _user("bob", job_count=10, avg_memory_request=1024.0,
              avg_memory_usage_kb=0.0, avg_wall_time=3600.0),
    ]
    results = m.rank_by_wasted_memory_gb_hours(users)
    assert results[0]["user"] == "bob"


def test_rank_by_wasted_memory_gb_hours_zero_waste_excluded():
    # request == usage → no waste
    user = _user(
        "alice",
        avg_memory_request=1024.0,
        avg_memory_usage_kb=1024.0 * 1024,  # 1024 MB in KB = 1024 * 1024 KB
        avg_wall_time=3600.0,
    )
    assert m.rank_by_wasted_memory_gb_hours([user]) == []


def test_rank_by_wasted_memory_gb_hours_includes_memory_fields():
    user = _user(
        "alice",
        job_count=1,
        avg_memory_request=1024.0,
        avg_memory_usage_kb=0.0,
        avg_wall_time=3600.0,
    )
    result = m.rank_by_wasted_memory_gb_hours([user])[0]
    assert "wasted_gb_hours" in result
    assert "avg_memory_request_mb" in result


def test_rank_by_wasted_memory_gb_hours_empty_input():
    assert m.rank_by_wasted_memory_gb_hours([]) == []
