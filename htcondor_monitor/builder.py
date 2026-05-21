"""
Prompt construction.

Each monitoring task has a Jinja2 prompt template.  The PromptBuilder
renders it with:
  - current thresholds from Settings
  - ClassAd field names from Settings (so the agent uses the right names)
  - prior run context from StateStore
  - current timestamp and cadence

The rendered prompt is passed directly to the CodeAgent as its task string.
"""

from __future__ import annotations

from datetime import datetime, timezone
from textwrap import dedent

from jinja2 import Environment, BaseLoader

from .settings import settings
from .state.store import StateStore


# ── Raw prompt templates ───────────────────────────────────────────────────────
# Stored as module-level strings so the package has no external file dependency.
# Each template receives the `cfg` (Settings) and `prior` (str) variables.

TEMPLATES: dict[str, str] = {}

TEMPLATES["health_check"] = dedent("""\
You are an HTCondor cluster monitoring agent.  You have access to tools that
query job ClassAd history stored in OpenSearch.

## Schema context
Key ClassAd fields available:
- Job identity : {{ cfg.field_user }}, {{ cfg.field_cluster_id }}, {{ cfg.field_proc_id }}
- Timing       : {{ cfg.field_submit_time }} (epoch), {{ cfg.field_start_time }}, {{ cfg.field_completion_time }}
- CPU          : {{ cfg.field_cpu_user }} (s), {{ cfg.field_cpu_sys }} (s), {{ cfg.field_wall_time }} (s)
- Memory       : {{ cfg.field_memory_request }} (MB requested), {{ cfg.field_memory_usage }} (KB actual RSS)
- Disk         : {{ cfg.field_disk_request }} (KB), {{ cfg.field_disk_usage }} (KB)
- Status       : {{ cfg.field_job_status }} (4=completed, 5=held, 3=removed, 2=running, 1=idle)
- Hold info    : {{ cfg.field_hold_reason }}, {{ cfg.field_hold_reason_code }}
- Exit         : {{ cfg.field_exit_code }}
- Restarts     : {{ cfg.field_num_job_starts }}, {{ cfg.field_num_shadow_exceptions }}
- Placement    : {{ cfg.field_last_remote_host }}

Use get_index_field_names() if you need to discover additional fields.

## Task — Daily Health Check ({{ now }})
Query the last 24 hours of jobs and produce a structured report.

Identify and summarise:
1. Jobs that were held or removed — group by hold reason and user.
   Flag if hold rate > {{ cfg.cpu_efficiency_warn_pct }}% of a user's submissions.
2. Jobs where actual memory usage ({{ cfg.field_memory_usage }} KB / 1024 → MB)
   exceeded {{ cfg.field_memory_request }} by more than {{ cfg.memory_exceeded_pct }}%.
3. Jobs with CPU efficiency below {{ cfg.cpu_efficiency_warn_pct }}%
   ({{ cfg.field_cpu_user }} / {{ cfg.field_wall_time }} × 100).
4. Jobs evicted more than {{ cfg.eviction_warn_count }} times
   ({{ cfg.field_num_job_starts }} > {{ cfg.eviction_warn_count }}).
5. Execute nodes appearing in more than {{ cfg.node_failure_rate_pct }}% job failures — flag as potentially unhealthy.

For each finding include: user, job ID, submit time, and the specific metric.

## Prior run context
{{ prior }}

## Output format
Return your findings as a JSON object with this structure:
{
  "executive_summary": "<2-3 sentence plain-English summary>",
  "held_or_removed": [...],
  "memory_exceeded": [...],
  "low_cpu_efficiency": [...],
  "excessive_evictions": [...],
  "unhealthy_nodes": [...],
  "flagged_users": ["<username>", ...],
  "flagged_nodes": ["<nodename>", ...],
  "new_issues": [...],
  "ongoing_issues": [...],
  "resolved_issues": [...]
}
""")

TEMPLATES["resource_efficiency"] = dedent("""\
You are an HTCondor cluster monitoring agent with access to OpenSearch job history.

## Schema context
{{ cfg.field_user }}, {{ cfg.field_memory_request }} (MB), {{ cfg.field_memory_usage }} (KB),
{{ cfg.field_disk_request }} (KB), {{ cfg.field_disk_usage }} (KB),
{{ cfg.field_cpus_request }}, {{ cfg.field_cpu_user }} (s), {{ cfg.field_wall_time }} (s),
{{ cfg.field_job_status }}, {{ cfg.field_num_job_starts }}

## Task — Weekly Resource Efficiency Review ({{ now }})
Query the last 7 days.  For each user with at least 5 jobs:

1. Average CPU efficiency = {{ cfg.field_cpu_user }} / {{ cfg.field_wall_time }} × 100.
2. Memory accuracy = mean({{ cfg.field_memory_usage }} KB / 1024) / mean({{ cfg.field_memory_request }}).
3. Disk accuracy = mean({{ cfg.field_disk_usage }}) / mean({{ cfg.field_disk_request }}).
4. Hold / removal / eviction rates vs completions.
5. Flag users whose memory or CPU request exceeds actual usage by > {{ cfg.memory_overrequest_ratio }}×.

Rank users by estimated wasted CPU-hours and memory-GB-hours.
For the top 5 worst over-requesters suggest specific corrected values based on their p90 actual usage.

## Prior run context
{{ prior }}

## Output format
{
  "executive_summary": "...",
  "per_user_stats": [...],
  "top_wasters": [...],
  "recommendations": {"<user>": {"RequestMemory": <MB>, "RequestCpus": <n>}, ...},
  "flagged_users": [...],
  "new_issues": [...],
  "ongoing_issues": [...],
  "resolved_issues": [...]
}
""")

TEMPLATES["node_health"] = dedent("""\
You are an HTCondor cluster monitoring agent with access to OpenSearch job history.

## Schema context
{{ cfg.field_last_remote_host }}, {{ cfg.field_job_status }}, {{ cfg.field_hold_reason }},
{{ cfg.field_num_shadow_exceptions }}, {{ cfg.field_num_job_starts }}, {{ cfg.field_exit_code }},
{{ cfg.field_user }}, {{ cfg.field_cluster_id }}

## Task — Infrastructure / Execute Node Health ({{ now }})
Query the last 7 days.

1. Nodes with job failure rate > {{ cfg.node_failure_rate_pct }}%.
2. Nodes with above-average eviction rates.
3. Failure patterns correlated with machine features (OS, memory tier, GPU type) if those
   fields are present in the index.
4. Jobs with {{ cfg.field_num_shadow_exceptions }} > 0 — group by node and user.
5. Non-zero, non-signal exit codes — group by executable/batch name to distinguish
   application errors from infrastructure errors.

For each suspect node state whether it should be investigated, drained, or offline.

## Prior run context
{{ prior }}

## Output format
{
  "executive_summary": "...",
  "suspect_nodes": [{"node": ..., "failure_rate_pct": ..., "recommendation": ...}, ...],
  "shadow_exception_clusters": [...],
  "exit_code_analysis": [...],
  "flagged_nodes": [...],
  "new_issues": [...],
  "ongoing_issues": [...],
  "resolved_issues": [...]
}
""")

TEMPLATES["long_running_jobs"] = dedent("""\
You are an HTCondor cluster monitoring agent.

## Schema context
{{ cfg.field_user }}, {{ cfg.field_cluster_id }}, {{ cfg.field_wall_time }},
{{ cfg.field_cpu_user }}, {{ cfg.field_num_job_starts }}, {{ cfg.field_memory_usage }},
{{ cfg.field_job_status }}

## Task — Long-Running Job Audit ({{ now }})
Find jobs whose wall time > {{ cfg.long_job_multiplier }}× their RequestWalltime
(or > {{ cfg.long_job_fallback_hours }} hours if RequestWalltime is unset).

For each:
1. CPU efficiency over its lifetime.
2. Number of evictions / restarts.
3. Whether checkpointing is active (look for OnExitRemove or CheckpointedAt fields).
4. Whether memory usage appears to be growing (compare early vs recent RSS samples if available).

Classify each as: GOOD_USE, STALLED, LEAKING_MEMORY, or NEEDS_REVIEW.
Flag for user notification or admin intervention where appropriate.

## Prior run context
{{ prior }}

## Output format
{
  "executive_summary": "...",
  "long_running_jobs": [{"job_id": ..., "user": ..., "wall_hours": ..., "classification": ..., "action": ...}, ...],
  "flagged_users": [...],
  "new_issues": [...],
  "ongoing_issues": [...],
  "resolved_issues": [...]
}
""")

TEMPLATES["user_behavior_trends"] = dedent("""\
You are an HTCondor cluster monitoring agent.

## Schema context
{{ cfg.field_user }}, {{ cfg.field_submit_time }}, {{ cfg.field_memory_request }},
{{ cfg.field_cpus_request }}, {{ cfg.field_gpus_request }}, {{ cfg.field_job_status }},
{{ cfg.field_num_job_starts }}

## Task — User Behaviour Trend Analysis ({{ now }}, {{ cadence }})
Compare the current {{ period_label }} against the equivalent prior period.

1. Large increase in submission rate — possible runaway scripts (flag > 3× baseline).
2. Sudden drop in CPU efficiency vs user's own history.
3. Shift in request sizes without matching actual usage change.
4. Increase in hold / removal rate vs user's own history.
5. New users with no prior history — flag for onboarding review.

Flag anomalies deviating > {{ cfg.anomaly_stddev_threshold }} SD from the user's baseline.
Ignore changes consistent with known scheduled workflow patterns if you can infer them
from the submission timing (e.g., jobs always spike on Mondays).

## Prior run context
{{ prior }}

## Output format
{
  "executive_summary": "...",
  "anomalous_users": [{"user": ..., "anomaly_type": ..., "current_value": ..., "baseline_value": ..., "severity": ...}, ...],
  "new_users": [...],
  "flagged_users": [...],
  "new_issues": [...],
  "ongoing_issues": [...],
  "resolved_issues": [...]
}
""")

TEMPLATES["gpu_utilization"] = dedent("""\
You are an HTCondor cluster monitoring agent.

## Schema context
{{ cfg.field_user }}, {{ cfg.field_gpus_request }}, {{ cfg.field_last_remote_host }},
{{ cfg.field_job_status }}, {{ cfg.field_cpu_user }}, {{ cfg.field_wall_time }}
Look for GPU utilisation custom attributes such as GpuUsage, AssignedGpus, CUDADeviceName.

## Task — GPU Utilisation Review ({{ now }})
Filter to jobs with {{ cfg.field_gpus_request }} >= 1 over the last 7 days.

1. Compare RequestGpus to actual GPU utilisation (custom attributes if present).
2. Jobs that requested GPUs but show < {{ cfg.gpu_idle_threshold_pct }}% GPU activity
   (likely CPU-only code on GPU slots).
3. Average GPU slot occupancy per execute node.
4. Users who consistently under-utilise GPUs.

GPU slots are scarce — produce a ranked list of GPU waste by user and suggest
whether they should use CPU-only slots instead.

## Prior run context
{{ prior }}

## Output format
{
  "executive_summary": "...",
  "gpu_waste_by_user": [{"user": ..., "wasted_gpu_hours": ..., "recommendation": ...}, ...],
  "node_gpu_occupancy": [...],
  "flagged_users": [...],
  "new_issues": [...],
  "ongoing_issues": [...],
  "resolved_issues": [...]
}
""")

TEMPLATES["anomaly_detection"] = dedent("""\
You are an HTCondor cluster monitoring agent.

## Schema context
{{ cfg.field_user }}, {{ cfg.field_cluster_id }}, {{ cfg.field_wall_time }},
{{ cfg.field_memory_usage }}, {{ cfg.field_num_job_starts }}, {{ cfg.field_hold_reason }},
{{ cfg.field_job_status }}, {{ cfg.field_submit_time }}

## Task — Anomaly / Outlier Detection ({{ now }})
Query the last 24 hours.

1. Jobs with wall time > 99th percentile for their job class or user.
2. Jobs with memory usage > 99th percentile.
3. Jobs with {{ cfg.field_num_job_starts }} > 5.
4. Clusters of jobs submitted in a short window that all failed with the same
   hold reason — may indicate shared misconfiguration.
5. Sudden spikes in queue depth for any single user.

For each outlier note: is this unusual for this specific user, or globally unusual?
Has this pattern appeared in prior runs?

## Prior run context
{{ prior }}

## Output format
{
  "executive_summary": "...",
  "wall_time_outliers": [...],
  "memory_outliers": [...],
  "excessive_restarts": [...],
  "failure_clusters": [...],
  "queue_spikes": [...],
  "flagged_users": [...],
  "new_issues": [...],
  "ongoing_issues": [...],
  "resolved_issues": [...]
}
""")


# ── Builder ────────────────────────────────────────────────────────────────────

class PromptBuilder:
    """Renders a named prompt template with live settings and prior context."""

    _env = Environment(loader=BaseLoader(), autoescape=False)

    def __init__(self, state_store: StateStore | None = None):
        self._store = state_store or StateStore()

    @property
    def available_tasks(self) -> list[str]:
        return list(TEMPLATES.keys())

    def build(
        self,
        task_name: str,
        cadence: str | None = None,
        extra_vars: dict | None = None,
    ) -> str:
        """
        Render the prompt template for *task_name*.

        Args:
            task_name: One of the keys in TEMPLATES.
            cadence: Override the global settings cadence.
            extra_vars: Additional variables passed to the Jinja2 template.

        Returns:
            Fully rendered prompt string ready to pass to the agent.
        """
        if task_name not in TEMPLATES:
            raise ValueError(
                f"Unknown task '{task_name}'.  Available: {self.available_tasks}"
            )

        cadence = cadence or settings.cadence
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # Period label for trend tasks
        period_map = {"daily": "24 hours", "weekly": "7 days", "monthly": "30 days"}
        period_label = period_map.get(cadence, "period")

        prior = self._store.prior_context_block(cadence, task_name)

        ctx = {
            "cfg": settings,
            "cadence": cadence,
            "now": now,
            "period_label": period_label,
            "prior": prior,
        }
        if extra_vars:
            ctx.update(extra_vars)

        template = self._env.from_string(TEMPLATES[task_name])
        return template.render(**ctx)
