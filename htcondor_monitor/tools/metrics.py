"""
metrics.py — deterministic threshold analysis over OpenSearch query results.

Each function takes raw query results and settings thresholds and returns
structured finding dicts.  No LLM calls, no OpenSearch calls — pure Python.
These are independently testable with mock data.

The MonitoringAgent passes the output of these functions to the agent as
pre-computed context rather than having the agent run the queries itself.
"""

from __future__ import annotations

import logging
from typing import Any

from ..settings import settings

logger = logging.getLogger(__name__)

# Type alias for a finding dict
Finding = dict[str, Any]

# ── Top users  ─────────────────────────────────────────────────────────────────


def find_top_users(user_stats: list[dict], num_users: int = 5) -> list[Finding]:
    """
    Find the top users in terms of total wall time consumed.
    """
    findings = []

    def wall_time_used(row):
        """
        Negative of total wall time, so that high wall time users appear
        first in the list
        """
        return -(row.get("avg_wall_time") or 0) * row["job_count"]

    for row in sorted(user_stats, key=wall_time_used)[:num_users]:
        findings.append({"user": row["user"], "job_count": row["job_count"],
                         "total_wall_time": -wall_time_used(row)})
    return findings


# ── CPU efficiency ─────────────────────────────────────────────────────────────

def cpu_efficiency(cpu_user_s: float | None, wall_time_s: float | None) -> float | None:
    """Return CPU efficiency as a percentage, or None if inputs are unusable."""
    if not cpu_user_s or not wall_time_s or wall_time_s <= 0:
        return None
    return round(100.0 * cpu_user_s / wall_time_s, 1)


def find_low_cpu_efficiency(user_stats: list[dict]) -> list[Finding]:
    """
    Flag users whose average CPU efficiency is below the configured threshold.

    Args:
        user_stats: Output of fetch_user_aggregations().

    Returns:
        List of findings, one per flagged user.
    """
    findings = []
    for row in user_stats:
        eff = cpu_efficiency(row.get("avg_cpu_user"), row.get("avg_wall_time"))
        if eff is not None and eff < settings.cpu_efficiency_warn_pct:
            findings.append({
                "user":                row["user"],
                "job_count":           row["job_count"],
                "avg_cpu_efficiency_pct": eff,
                "threshold_pct":       settings.cpu_efficiency_warn_pct,
            })
    return sorted(findings, key=lambda f: f["avg_cpu_efficiency_pct"])


# ── Memory ─────────────────────────────────────────────────────────────────────

def memory_usage_mb(usage_kb: float | None) -> float | None:
    return round(usage_kb / 1024, 1) if usage_kb else None


def memory_overrequest_ratio(
    usage_kb: float | None,
    request_mb: float | None,
) -> float | None:
    if not usage_kb or not request_mb or request_mb <= 0:
        return None
    usage_mb = usage_kb / 1024
    return round(usage_mb / request_mb, 2)


def find_memory_exceeded(user_stats: list[dict]) -> list[Finding]:
    """
    Flag users whose average memory usage exceeded their request by more
    than the configured percentage.
    """
    findings = []
    threshold = 1.0 + settings.memory_exceeded_pct / 100.0
    for row in user_stats:
        ratio = memory_overrequest_ratio(
            row.get("avg_memory_usage_kb"),
            row.get("avg_memory_request"),
        )
        if ratio is not None and ratio > threshold:
            findings.append({
                "user":                  row["user"],
                "job_count":             row["job_count"],
                "avg_memory_request_mb": row.get("avg_memory_request"),
                "avg_memory_usage_mb":   memory_usage_mb(row.get("avg_memory_usage_kb")),
                "usage_ratio":           ratio,
                "exceeded_by_pct":       round((ratio - 1.0) * 100, 1),
            })
    return sorted(findings, key=lambda f: f["usage_ratio"], reverse=True)


def find_memory_overrequested(user_stats: list[dict]) -> list[Finding]:
    """
    Flag users who are over-requesting memory by more than the configured ratio,
    with suggested corrected values based on their p90 actual usage.
    """
    findings = []
    for row in user_stats:
        ratio = memory_overrequest_ratio(
            row.get("avg_memory_usage_kb"),
            row.get("avg_memory_request"),
        )
        if ratio is not None and ratio > 0 and (1.0 / ratio) > settings.memory_overrequest_ratio:
            p90_kb = row.get("avg_memory_usage_kb_p90")
            suggested_mb = round(p90_kb / 1024 * 1.25) if p90_kb else None  # p90 + 25% headroom
            findings.append({
                "user":                  row["user"],
                "job_count":             row["job_count"],
                "avg_memory_request_mb": row.get("avg_memory_request"),
                "avg_memory_usage_mb":   memory_usage_mb(row.get("avg_memory_usage_kb")),
                "overrequest_ratio":     round(1.0 / ratio, 2),
                "suggested_request_mb":  suggested_mb,
            })
    return sorted(findings, key=lambda f: f["overrequest_ratio"], reverse=True)


# ── Hold / removal rates ───────────────────────────────────────────────────────

def find_high_hold_rates(user_stats: list[dict]) -> list[Finding]:
    """Flag users with a hold rate above the configured threshold."""
    findings = []
    for row in user_stats:
        total = row["job_count"]
        if total < 5:
            continue
        held = row.get("held_jobs", 0)
        rate = round(100.0 * held / total, 1)
        if rate > settings.cpu_efficiency_warn_pct:   # reuses warn threshold
            findings.append({
                "user":         row["user"],
                "job_count":    total,
                "held_jobs":    held,
                "hold_rate_pct": rate,
            })
    return sorted(findings, key=lambda f: f["hold_rate_pct"], reverse=True)


def find_excessive_evictions(user_stats: list[dict]) -> list[Finding]:
    """Flag users whose jobs are being restarted excessively on average."""
    findings = []
    for row in user_stats:
        total = row["job_count"]
        if total < 1:
            continue
        starts = row.get("total_job_starts", 0)
        # NumJobStarts includes the first start, so evictions = starts - jobs
        evictions = max(0, starts - total)
        avg_evictions = round(evictions / total, 2)
        if avg_evictions > settings.eviction_warn_count:
            findings.append({
                "user":            row["user"],
                "job_count":       total,
                "total_evictions": evictions,
                "avg_evictions_per_job": avg_evictions,
            })
    return sorted(findings, key=lambda f: f["avg_evictions_per_job"], reverse=True)


# ── Node health ────────────────────────────────────────────────────────────────

def find_unhealthy_nodes(node_stats: list[dict]) -> list[Finding]:
    """Flag nodes whose job failure rate exceeds the configured threshold."""
    return [
        {
            "node":             row["node"],
            "total_jobs":       row["total_jobs"],
            "failed_jobs":      row["failed_jobs"],
            "failure_rate_pct": row["failure_rate_pct"],
            "shadow_exceptions": row["shadow_exceptions"],
            "recommendation":   (
                "drain and investigate"
                if row["failure_rate_pct"] > settings.node_failure_rate_pct * 2
                else "monitor closely"
            ),
        }
        for row in node_stats
        if row["failure_rate_pct"] > settings.node_failure_rate_pct
    ]


def classify_exit_codes(node_stats: list[dict]) -> list[Finding]:
    """
    Separate non-zero exit codes into likely application errors vs
    infrastructure errors based on the node's overall failure rate.
    High-failure nodes with non-zero exits are more likely infrastructure.
    """
    findings = []
    for row in node_stats:
        non_zero = {
            code: count
            for code, count in row.get("exit_codes", {}).items()
            if code not in (0, None)
        }
        if not non_zero:
            continue
        likely_infra = row["failure_rate_pct"] > settings.node_failure_rate_pct
        findings.append({
            "node":          row["node"],
            "exit_codes":    non_zero,
            "failure_rate_pct": row["failure_rate_pct"],
            "likely_cause":  "infrastructure" if likely_infra else "application",
        })
    return findings


# ── Anomaly detection ──────────────────────────────────────────────────────────

def find_anomalous_users(
    current_stats: list[dict],
    prior_stats: list[dict],
) -> list[Finding]:
    """
    Compare current per-user stats against prior period stats and flag
    users who deviate more than anomaly_stddev_threshold standard deviations
    from their own recent baseline.

    Args:
        current_stats: Output of fetch_user_aggregations() for current period.
        prior_stats: Output of fetch_user_aggregations() for prior period.

    Returns:
        List of anomaly findings.
    """
    prior_by_user = {row["user"]: row for row in prior_stats}
    findings = []

    for row in current_stats:
        user = row["user"]
        prior = prior_by_user.get(user)
        if not prior:
            continue  # new users handled separately

        anomalies = []

        # Submission rate change
        rate_change = row["job_count"] / prior["job_count"] if prior["job_count"] else None
        if rate_change and rate_change > 3.0:
            anomalies.append({
                "metric":          "submission_rate",
                "current":         row["job_count"],
                "prior":           prior["job_count"],
                "change_ratio":    round(rate_change, 2),
                "description":     f"Job count increased {rate_change:.1f}× vs prior period",
            })

        # CPU efficiency drop
        curr_eff = cpu_efficiency(row.get("avg_cpu_user"), row.get("avg_wall_time"))
        prior_eff = cpu_efficiency(prior.get("avg_cpu_user"), prior.get("avg_wall_time"))
        if curr_eff is not None and prior_eff is not None and prior_eff > 0:
            drop = prior_eff - curr_eff
            if drop > settings.anomaly_stddev_threshold * 10:  # >20ppt drop as proxy
                anomalies.append({
                    "metric":      "cpu_efficiency",
                    "current_pct": curr_eff,
                    "prior_pct":   prior_eff,
                    "drop_ppt":    round(drop, 1),
                    "description": f"CPU efficiency dropped {drop:.1f} percentage points",
                })

        # Hold rate increase
        curr_hold_rate = 100.0 * row.get("held_jobs", 0) / row["job_count"] if row["job_count"] else 0
        prior_hold_rate = 100.0 * prior.get("held_jobs", 0) / prior["job_count"] if prior["job_count"] else 0
        if curr_hold_rate > prior_hold_rate * 2 and curr_hold_rate > 5:
            anomalies.append({
                "metric":           "hold_rate",
                "current_pct":      round(curr_hold_rate, 1),
                "prior_pct":        round(prior_hold_rate, 1),
                "description":      "Hold rate doubled vs prior period",
            })

        if anomalies:
            findings.append({"user": user, "anomalies": anomalies})

    return findings


def find_new_users(
    current_stats: list[dict],
    prior_stats: list[dict],
) -> list[str]:
    """Return list of users present in current period but not in prior period."""
    prior_users = {row["user"] for row in prior_stats}
    return [
        row["user"]
        for row in current_stats
        if row["user"] not in prior_users
    ]


def _is_meaningful_outlier(
        value: float,
        p99: float | None,
        min_p99: float,
        ratio: float = 2.0
) -> bool:
    """
    Return True only if p99 itself indicates the metric has meaningful spread
    and the value substantially exceeds even that elevated baseline.
    """
    return (
        p99 is not None
        and p99 > min_p99
        and value > p99 * ratio
    )


def find_fleet_outliers(
    user_stats: list[dict],
    fleet_percentiles: dict[str, float],
) -> list[Finding]:
    """
    Flag users whose jobs exceed fleet-wide p99 thresholds for wall time,
    memory, or restart count.
    """
    findings = []
    p99_wall   = fleet_percentiles.get("wall_time_p99")
    p99_memory = fleet_percentiles.get("memory_kb_p99")
    p99_starts = fleet_percentiles.get("job_starts_p99")

    for row in user_stats:
        flags = []
        if p99_wall and row.get("avg_wall_time", 0) > p99_wall:
            flags.append(f"avg wall time {row['avg_wall_time']:.0f}s > p99 {p99_wall:.0f}s")
        try:
            if p99_memory and row.get("avg_memory_usage_kb", 0) > p99_memory:
                flags.append(f"avg memory {row['avg_memory_usage_kb']:.0f}KB > p99 {p99_memory:.0f}KB")
        except TypeError:
            pass
        if _is_meaningful_outlier(
                row.get("total_job_starts", 0) / max(row["job_count"], 1),
                p99_starts,
                min_p99=2.0,
        ):
            flags.append(
                f"avg job starts per job {row['total_job_starts']/row['job_count']:.1f} "
                f"is more than 2x the p99 fleet baseline of {p99_starts:.1f}"
            )
        if flags:
            findings.append({"user": row["user"], "job_count": row["job_count"], "flags": flags})
    return findings


# ── Resource waste ranking ─────────────────────────────────────────────────────

def rank_by_wasted_cpu_hours(user_stats: list[dict]) -> list[Finding]:
    """
    Rank users by estimated wasted CPU-hours — wall time consumed minus
    actual CPU time used, weighted by job count.
    """
    ranked = []
    for row in user_stats:
        wall = row.get("avg_wall_time") or 0
        cpu  = row.get("avg_cpu_user")  or 0
        jobs = row["job_count"]
        wasted_cpu_hours = round((wall - cpu) * jobs / 3600, 2)
        if wasted_cpu_hours > 0:
            ranked.append({
                "user":              row["user"],
                "job_count":         jobs,
                "wasted_cpu_hours":  wasted_cpu_hours,
                "avg_cpu_eff_pct":   cpu_efficiency(cpu, wall),
            })
    return sorted(ranked, key=lambda r: r["wasted_cpu_hours"], reverse=True)


def rank_by_wasted_memory_gb_hours(user_stats: list[dict]) -> list[Finding]:
    """
    Rank users by estimated wasted memory GB-hours — requested minus used,
    weighted by average wall time and job count.
    """
    ranked = []
    for row in user_stats:
        req_mb   = row.get("avg_memory_request") or 0
        used_kb  = row.get("avg_memory_usage_kb") or 0
        wall     = row.get("avg_wall_time") or 0
        jobs     = row["job_count"]
        unused_mb = max(0, req_mb - used_kb / 1024)
        wasted_gb_hours = round(unused_mb / 1024 * wall / 3600 * jobs, 2)
        if wasted_gb_hours > 0:
            ranked.append({
                "user":                 row["user"],
                "job_count":            jobs,
                "wasted_gb_hours":      wasted_gb_hours,
                "avg_memory_request_mb": req_mb,
                "avg_memory_usage_mb":  round(used_kb / 1024, 1) if used_kb else None,
            })
    return sorted(ranked, key=lambda r: r["wasted_gb_hours"], reverse=True)
