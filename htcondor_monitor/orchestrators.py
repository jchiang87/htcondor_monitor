"""
orchestrators.py — one function per monitoring task.

Each orchestrator:
  1. Calls the query layer to fetch raw data from OpenSearch
  2. Calls the metrics layer to apply threshold logic
  3. Bundles the pre-computed findings into a context dict
  4. Returns that dict for injection into the agent prompt

The agent then receives structured findings as input rather than needing
to discover, query, and compute everything itself.  Its role is reduced to
narrative synthesis, cross-signal reasoning, and new/ongoing/resolved classification.
"""

from __future__ import annotations

import logging
from typing import Any

from htcondor_monitor.config.settings import settings
from htcondor_monitor.tools import opensearch_queries as q
from htcondor_monitor.tools import metrics as m

logger = logging.getLogger(__name__)

FindingsContext = dict[str, Any]


# ── Health check ───────────────────────────────────────────────────────────────

def run_health_check(hours_back: int = 24) -> FindingsContext:
    """
    Pre-compute all findings for the daily health check task.
    Returns a structured dict ready for injection into the agent prompt.
    """
    logger.info("health_check: fetching user aggregations")
    user_stats = q.fetch_user_aggregations(hours_back=hours_back)

    logger.info("health_check: fetching node aggregations")
    node_stats = q.fetch_node_aggregations(hours_back=hours_back)

    logger.info("health_check: fetching hold reasons")
    hold_reasons = q.fetch_hold_reasons(hours_back=hours_back)

    logger.info("health_check: computing metrics")
    return {
        "hours_back":          hours_back,
        "total_users":         len(user_stats),
        "total_nodes":         len(node_stats),
        "low_cpu_efficiency":  m.find_low_cpu_efficiency(user_stats),
        "memory_exceeded":     m.find_memory_exceeded(user_stats),
        "high_hold_rates":     m.find_high_hold_rates(user_stats),
        "excessive_evictions": m.find_excessive_evictions(user_stats),
        "unhealthy_nodes":     m.find_unhealthy_nodes(node_stats),
        "hold_reason_summary": hold_reasons,
        "exit_code_analysis":  m.classify_exit_codes(node_stats),
        "thresholds": {
            "cpu_efficiency_warn_pct":  settings.cpu_efficiency_warn_pct,
            "memory_exceeded_pct":      settings.memory_exceeded_pct,
            "eviction_warn_count":      settings.eviction_warn_count,
            "node_failure_rate_pct":    settings.node_failure_rate_pct,
        },
    }


# ── Resource efficiency ────────────────────────────────────────────────────────

def run_resource_efficiency(hours_back: int = 168) -> FindingsContext:
    """Pre-compute weekly resource efficiency findings."""
    logger.info("resource_efficiency: fetching user aggregations")
    user_stats = q.fetch_user_aggregations(hours_back=hours_back)

    # Filter to users with at least 5 jobs
    active = [r for r in user_stats if r["job_count"] >= 5]
    logger.info("resource_efficiency: %d active users", len(active))

    return {
        "hours_back":              hours_back,
        "active_user_count":       len(active),
        "low_cpu_efficiency":      m.find_low_cpu_efficiency(active),
        "memory_exceeded":         m.find_memory_exceeded(active),
        "memory_overrequested":    m.find_memory_overrequested(active),
        "excessive_evictions":     m.find_excessive_evictions(active),
        "wasted_cpu_hours_ranked": m.rank_by_wasted_cpu_hours(active)[:20],
        "wasted_memory_ranked":    m.rank_by_wasted_memory_gb_hours(active)[:20],
        "per_user_stats":          active,
        "thresholds": {
            "cpu_efficiency_warn_pct":   settings.cpu_efficiency_warn_pct,
            "memory_overrequest_ratio":  settings.memory_overrequest_ratio,
            "memory_exceeded_pct":       settings.memory_exceeded_pct,
        },
    }


# ── Node health ────────────────────────────────────────────────────────────────

def run_node_health(hours_back: int = 168) -> FindingsContext:
    """Pre-compute weekly node health findings."""
    logger.info("node_health: fetching node aggregations")
    node_stats = q.fetch_node_aggregations(hours_back=hours_back)

    logger.info("node_health: fetching hold reasons")
    hold_reasons = q.fetch_hold_reasons(hours_back=hours_back)

    unhealthy = m.find_unhealthy_nodes(node_stats)
    exit_analysis = m.classify_exit_codes(node_stats)

    return {
        "hours_back":            hours_back,
        "total_nodes":           len(node_stats),
        "unhealthy_nodes":       unhealthy,
        "exit_code_analysis":    exit_analysis,
        "hold_reason_summary":   hold_reasons,
        "all_node_stats":        node_stats[:50],  # top 50 by failure rate
        "thresholds": {
            "node_failure_rate_pct": settings.node_failure_rate_pct,
        },
    }


# ── Anomaly detection ──────────────────────────────────────────────────────────

def run_anomaly_detection(hours_back: int = 24) -> FindingsContext:
    """
    Pre-compute anomaly detection findings.
    Fetches current and prior period stats in two queries, then compares.
    """
    logger.info("anomaly_detection: fetching current period user aggregations")
    current_stats = q.fetch_user_aggregations(hours_back=hours_back)

    logger.info("anomaly_detection: fetching prior period user aggregations")
    prior_stats = q.fetch_user_aggregations(hours_back=hours_back * 2)

    logger.info("anomaly_detection: fetching fleet percentiles")
    fleet_pcts = q.fetch_fleet_percentiles(hours_back=hours_back)

    logger.info("anomaly_detection: fetching hold reasons")
    hold_reasons = q.fetch_hold_reasons(hours_back=hours_back)

    anomalous = m.find_anomalous_users(current_stats, prior_stats)
    new_users  = m.find_new_users(current_stats, prior_stats)
    outliers   = m.find_fleet_outliers(current_stats, fleet_pcts)

    return {
        "hours_back":          hours_back,
        "total_users":         len(current_stats),
        "fleet_percentiles":   fleet_pcts,
        "anomalous_users":     anomalous,
        "new_users":           new_users,
        "fleet_outliers":      outliers,
        "hold_reason_summary": hold_reasons,
        "excessive_evictions": m.find_excessive_evictions(current_stats),
        "thresholds": {
            "anomaly_stddev_threshold": settings.anomaly_stddev_threshold,
            "cpu_efficiency_warn_pct":  settings.cpu_efficiency_warn_pct,
        },
    }


# ── User behaviour trends ──────────────────────────────────────────────────────

def run_user_behavior_trends(hours_back: int = 168) -> FindingsContext:
    """Pre-compute weekly/monthly user behaviour trend findings."""
    logger.info("user_behavior_trends: fetching current and prior period stats")
    current_stats = q.fetch_user_aggregations(hours_back=hours_back)
    prior_stats   = q.fetch_user_aggregations(hours_back=hours_back * 2)

    return {
        "hours_back":        hours_back,
        "current_user_count": len(current_stats),
        "prior_user_count":   len(prior_stats),
        "anomalous_users":    m.find_anomalous_users(current_stats, prior_stats),
        "new_users":          m.find_new_users(current_stats, prior_stats),
        "low_cpu_efficiency": m.find_low_cpu_efficiency(current_stats),
        "memory_overrequested": m.find_memory_overrequested(current_stats),
        "wasted_cpu_hours_ranked": m.rank_by_wasted_cpu_hours(current_stats)[:10],
        "thresholds": {
            "anomaly_stddev_threshold": settings.anomaly_stddev_threshold,
        },
    }


# ── GPU utilisation ────────────────────────────────────────────────────────────

def run_gpu_utilization(hours_back: int = 168) -> FindingsContext:
    """
    Pre-compute GPU utilisation findings.
    Filters to jobs that requested at least one GPU.
    """
    logger.info("gpu_utilization: fetching GPU job aggregations")
    gpu_filter = [{"range": {settings.field_gpus_request: {"gte": 1}}}]
    user_stats = q.fetch_user_aggregations(
        hours_back=hours_back,
        extra_aggs={
            "avg_gpus_requested": {"avg": {"field": settings.field_gpus_request}},
        },
    )
    node_stats = q.fetch_node_aggregations(hours_back=hours_back)

    # Filter to users who requested GPUs
    # (aggregate_by_user doesn't filter by GPU request; narrow in Python)
    # A follow-up raw query would be needed for precise GPU-only stats;
    # for now flag users with GPU requests based on available agg data.
    gpu_users = [r for r in user_stats if r.get("avg_gpus_requested", 0) >= 1]

    return {
        "hours_back":              hours_back,
        "gpu_user_count":          len(gpu_users),
        "low_cpu_efficiency":      m.find_low_cpu_efficiency(gpu_users),
        "wasted_cpu_hours_ranked": m.rank_by_wasted_cpu_hours(gpu_users)[:20],
        "node_stats":              node_stats[:20],
        "gpu_user_stats":          gpu_users,
        "note": (
            "GPU-specific utilisation metrics (e.g. GpuUsage) require custom "
            "ClassAd attributes from a GPU monitoring shim.  If those fields "
            "are present in your index they will appear in gpu_user_stats."
        ),
        "thresholds": {
            "gpu_idle_threshold_pct": settings.gpu_idle_threshold_pct,
        },
    }


# ── Long-running jobs ──────────────────────────────────────────────────────────

def run_long_running_jobs(hours_back: int = 168) -> FindingsContext:
    """
    Identify jobs whose wall time significantly exceeds their requested wall time.
    Returns raw job records for the agent to classify individually.
    """
    logger.info("long_running_jobs: fetching running and recently completed jobs")

    fallback_s = int(settings.long_job_fallback_hours * 3600)

    # Fetch running jobs with high wall time
    running = q.fetch_jobs(
        hours_back=hours_back,
        job_status=[2],  # running
        size=200,
        extra_filters=[
            {"range": {settings.field_wall_time: {"gte": fallback_s}}}
        ],
    )

    # Fetch completed jobs that took much longer than expected
    completed = q.fetch_jobs(
        hours_back=hours_back,
        job_status=[4],
        size=200,
        extra_filters=[
            {"range": {settings.field_wall_time: {"gte": fallback_s}}}
        ],
    )

    return {
        "hours_back":             hours_back,
        "long_running_count":     len(running),
        "long_completed_count":   len(completed),
        "long_running_jobs":      running,
        "long_completed_jobs":    completed,
        "thresholds": {
            "long_job_multiplier":      settings.long_job_multiplier,
            "long_job_fallback_hours":  settings.long_job_fallback_hours,
        },
        "note": (
            "Classify each job as GOOD_USE, STALLED, LEAKING_MEMORY, or NEEDS_REVIEW "
            "based on CPU efficiency, eviction count, and whether checkpointing is active. "
            "Look for OnExitRemove or CheckpointedAt fields in the job records."
        ),
    }


# ── Registry ───────────────────────────────────────────────────────────────────
# Maps task names to their orchestrator functions and default hours_back values.

ORCHESTRATORS: dict[str, tuple[callable, int]] = {
    "health_check":         (run_health_check,         24),
    "resource_efficiency":  (run_resource_efficiency,  168),
    "node_health":          (run_node_health,           168),
    "anomaly_detection":    (run_anomaly_detection,     24),
    "user_behavior_trends": (run_user_behavior_trends,  168),
    "gpu_utilization":      (run_gpu_utilization,       168),
    "long_running_jobs":    (run_long_running_jobs,     168),
}


def run_orchestrator(task_name: str, hours_back: int | None = None) -> FindingsContext:
    """
    Run the orchestrator for a named task and return its findings context.

    Args:
        task_name: One of the keys in ORCHESTRATORS.
        hours_back: Override the default lookback window.

    Returns:
        FindingsContext dict ready for injection into the agent prompt.
    """
    if task_name not in ORCHESTRATORS:
        raise ValueError(
            f"Unknown task '{task_name}'.  Available: {list(ORCHESTRATORS.keys())}"
        )
    fn, default_hours = ORCHESTRATORS[task_name]
    return fn(hours_back=hours_back or default_hours)
