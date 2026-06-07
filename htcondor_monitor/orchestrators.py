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
import json
import random
from typing import Any

from .settings import settings
from .tools import opensearch_queries as q
from .tools import metrics as m

logger = logging.getLogger(__name__)

FindingsContext = dict[str, Any]


def _sample(jobs: list[dict], n: int) -> tuple[list[dict], int]:
    """
    Return a random sample of at most n jobs and the original total count.
    The total is returned separately so the agent can report accurately
    even though it only sees a subset.
    """
    total = len(jobs)
    if total <= n:
        return jobs, total
    return random.sample(jobs, n), total


class ContextTooLargeError(RuntimeError):
    """Raised when pre-computed findings would exceed the configured token budget."""
    def __init__(self, task_name: str, estimated_tokens: int, limit: int):
        self.task_name = task_name
        self.estimated_tokens = estimated_tokens
        self.limit = limit
        super().__init__(
            f"Task '{task_name}' findings estimated at ~{estimated_tokens} tokens "
            f"which exceeds the limit of {limit}. "
            f"Increase HTCONDOR_MAX_FINDINGS_TOKENS or reduce the lookback window. "
            f"Aborting before LLM call."
        )


def _check_context_size(task_name: str, findings: FindingsContext) -> None:
    """
    Estimate the token count of the findings dict and raise ContextTooLargeError
    if it exceeds the configured limit.

    Uses the rough heuristic of len(json_bytes) / 4, which is a reliable
    conservative estimate for mixed English/numeric JSON.
    """
    serialised = json.dumps(findings, default=str)
    estimated_tokens = len(serialised) // 4
    limit = settings.max_findings_tokens
    if estimated_tokens > limit:
        raise ContextTooLargeError(task_name, estimated_tokens, limit)
    logger.debug(
        "Context size check passed for task=%s: ~%d tokens (limit %d)",
        task_name, estimated_tokens, limit,
    )


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
    exit_codes, exit_code_count = _sample(m.classify_exit_codes(node_stats), 50)
    return {
        "hours_back":          hours_back,
        "total_users":         len(user_stats),
        "total_nodes":         len(node_stats),
        "top_users":           m.find_top_users(user_stats),
        "low_cpu_efficiency":  m.find_low_cpu_efficiency(user_stats),
        "memory_exceeded":     m.find_memory_exceeded(user_stats),
        "high_hold_rates":     m.find_high_hold_rates(user_stats),
        "excessive_evictions": m.find_excessive_evictions(user_stats),
        "unhealthy_nodes":     m.find_unhealthy_nodes(node_stats),
        "hold_reason_summary": hold_reasons,
        "exit_code_analysis":  {"exit_code_sample": exit_codes,
                                "total_exit_code_count": exit_code_count},
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
    exit_codes, exit_code_count = _sample(m.classify_exit_codes(node_stats), 50)

    return {
        "hours_back":            hours_back,
        "total_nodes":           len(node_stats),
        "unhealthy_nodes":       unhealthy,
        "exit_code_analysis":  {"exit_code_sample": exit_codes,
                                "total_exit_code_count": exit_code_count},
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

_LONG_JOB_FIELDS = (
    settings.field_cluster_id,
    settings.field_proc_id,
    settings.field_user,
    settings.field_wall_time,
    settings.field_cpu_user,
    settings.field_cpu_sys,
    settings.field_num_job_starts,
    settings.field_memory_usage,
    settings.field_memory_request,
    settings.field_exit_code,
    "RequestWalltime",
    "OnExitRemove",
    "CheckpointedAt",
)


def _slim_job(job: dict) -> dict:
    """Return only the fields the agent needs for classification."""
    return {k: job[k] for k in _LONG_JOB_FIELDS if k in job}



def classify_long_running_jobs(jobs: list[dict]) -> dict[str, list]:
    """
    Pre-classify long-running jobs into buckets so the agent only needs
    to review edge cases rather than every job.

    Classification priority (first match wins):
      leaking_memory  — memory usage growing, above request, or RSS anomalously high
      terminated      — non-zero exit code or signal termination
      eviction_issues — excessive restarts without forward progress
      stalled         — very low CPU efficiency with no checkpointing
      good_use        — healthy CPU efficiency
      needs_review    — everything else

    Returns a dict with one list per classification bucket.
    Each job dict is slimmed to relevant fields and annotated with
    _cpu_eff_pct, _checkpointing, _evictions, and _classification_reason.
    """
    buckets: dict[str, list] = {
        "leaking_memory":  [],
        "terminated":      [],
        "eviction_issues": [],
        "stalled":         [],
        "good_use":        [],
        "needs_review":    [],
    }

    F = settings

    for job in jobs:
        wall       = job.get(F.field_wall_time)      or 0
        cpu        = job.get(F.field_cpu_user)        or 0
        rss_kb     = job.get(F.field_memory_usage)    or 0
        req_mb     = job.get(F.field_memory_request)  or 0
        num_starts = job.get(F.field_num_job_starts)  or 1
        exit_code  = job.get(F.field_exit_code)
        job_status = job.get(F.field_job_status)

        eff = round(100.0 * cpu / wall, 1) if wall > 0 else None
        checkpointing = bool(job.get("CheckpointedAt") or job.get("OnExitRemove"))
        # NumJobStarts includes the first start, so evictions = starts - 1
        evictions = max(0, num_starts - 1)

        slim = _slim_job(job)
        slim["_cpu_eff_pct"]    = eff
        slim["_checkpointing"]  = checkpointing
        slim["_evictions"]      = evictions

        # ── Memory leak / overuse ────────────────────────────────────────
        # Flag if RSS exceeds request, or if RSS is anomalously high
        # relative to CPU work done (high RSS + low CPU suggests leak).
        rss_mb = rss_kb / 1024
        memory_exceeded = req_mb > 0 and rss_mb > req_mb * (
            1 + settings.memory_exceeded_pct / 100
        )
        # Heuristic: RSS growing relative to CPU progress suggests a leak.
        # We approximate this as very high RSS combined with very low CPU
        # efficiency — the job is accumulating memory without doing work.
        likely_leaking = (
            rss_mb > 0
            and eff is not None
            and eff < 10
            and rss_mb > req_mb * 0.8   # using most of its allocation
        )
        if memory_exceeded or likely_leaking:
            slim["_classification_reason"] = (
                f"RSS {rss_mb:.0f}MB exceeds request {req_mb:.0f}MB"
                if memory_exceeded
                else f"High RSS {rss_mb:.0f}MB with low CPU efficiency {eff}%"
            )
            slim["_memory_exceeded"] = memory_exceeded
            slim["_likely_leaking"]  = likely_leaking
            buckets["leaking_memory"].append(slim)
            continue

        # ── Termination ──────────────────────────────────────────────────
        # Non-zero exit or signal kill (exit codes > 128 are signal + 128).
        # Status 4 = completed, so non-zero exit on a completed job is an
        # application error.  Status 3 = removed (external termination).
        is_signal_kill = isinstance(exit_code, int) and exit_code > 128
        is_app_error   = isinstance(exit_code, int) and 0 < exit_code <= 128
        is_removed     = job_status == 3
        if is_signal_kill or is_app_error or is_removed:
            slim["_classification_reason"] = (
                f"Signal kill (exit {exit_code}, signal {exit_code - 128})"
                if is_signal_kill
                else f"Application error (exit code {exit_code})"
                if is_app_error
                else "Job was removed externally"
            )
            slim["_exit_code"]   = exit_code
            slim["_is_removed"]  = is_removed
            buckets["terminated"].append(slim)
            continue

        # ── Eviction issues ──────────────────────────────────────────────
        # Flag if evictions are high relative to the job's wall time,
        # suggesting the job is being preempted repeatedly without making
        # progress.  Checkpointing mitigates this but repeated evictions
        # even with checkpointing may indicate a placement problem.
        eviction_rate = evictions / (wall / 3600) if wall > 0 else 0  # per hour
        excessive = evictions > settings.eviction_warn_count
        high_rate  = eviction_rate > 1.0  # more than once per hour
        if excessive or high_rate:
            slim["_classification_reason"] = (
                f"{evictions} evictions "
                f"({'checkpointing active' if checkpointing else 'no checkpointing'}), "
                f"rate {eviction_rate:.2f}/hr"
            )
            slim["_eviction_rate_per_hr"] = round(eviction_rate, 2)
            slim["_checkpointing"]        = checkpointing
            buckets["eviction_issues"].append(slim)
            continue

        # ── Stalled ──────────────────────────────────────────────────────
        if eff is not None and eff < 5 and not checkpointing:
            slim["_classification_reason"] = (
                f"CPU efficiency {eff}% with no checkpointing"
            )
            buckets["stalled"].append(slim)
            continue

        # ── Good use ─────────────────────────────────────────────────────
        if eff is not None and eff > 50:
            slim["_classification_reason"] = f"CPU efficiency {eff}%"
            buckets["good_use"].append(slim)
            continue

        # ── Needs review ─────────────────────────────────────────────────
        slim["_classification_reason"] = (
            f"CPU efficiency {eff}% — ambiguous"
            if eff is not None
            else "Insufficient data for classification"
        )
        buckets["needs_review"].append(slim)

    return buckets


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

    classified = classify_long_running_jobs(running + completed)
    result = {
        "hours_back": hours_back,
        "total_long_jobs": len(running) + len(completed),
        "good_use_count": len(classified["good_use"]),
        "thresholds": {
            "long_job_multiplier": settings.long_job_multiplier,
            "long_job_fallback_hours": settings.long_job_fallback_hours,
        },
        "note": (
            "Note: The lists for each of the problem categories -- leaking_memory, "
            "terminated, eviction_issues, stalled, and needs_review -- may be "
            "sampled from a larger set. The associated *_total entries give "
            "the true counts.  Report both the sample findings and the total "
            "counts in your summary."
        ),
    }
    # Build sampled buckets to limit context volume.
    for issue in classified:
        if issue == "good_use":
            continue
        subsample, total_count = _sample(classified[issue], 20)
        result[issue] = subsample
        result[f"{issue}_total"] = total_count

    return result


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
    findings = fn(hours_back=hours_back or default_hours)
    _check_context_size(task_name, findings)   # raises before returning if too large
    return findings
