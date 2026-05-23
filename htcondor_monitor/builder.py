"""
Prompt construction — hybrid architecture.

The PromptBuilder now operates in two modes:

  HYBRID (default): The orchestrator pre-computes findings via the metrics
  layer and injects them as structured context.  The agent's role is
  narrative synthesis, cross-signal reasoning, and new/ongoing/resolved
  classification.  Typically completes in 2-5 steps.

  AGENTIC (fallback): The original tool-driven prompts where the agent
  queries OpenSearch directly.  Used when pre-computation is not possible
  or when the agent needs to investigate something the metrics layer
  doesn't cover.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from textwrap import dedent

from jinja2 import Environment, BaseLoader

from .settings import settings
from .store import StateStore


# ── Hybrid prompt templates ────────────────────────────────────────────────────
# These receive a `findings` variable containing pre-computed metrics output.

HYBRID_TEMPLATES: dict[str, str] = {}

HYBRID_TEMPLATES["health_check"] = dedent("""\
You are an HTCondor cluster monitoring analyst.

## Pre-computed findings — Daily Health Check ({{ now }})
The following metrics have already been computed from the last {{ findings.hours_back }} hours
of job history.  You do not need to query OpenSearch.

{{ findings_json }}

## Prior run context
{{ prior }}

## Your tasks
1. Write a concise executive summary (2-3 sentences) highlighting the most important issues.
2. For each section of findings, explain what the numbers mean in plain English —
   don't just restate them.
3. Identify any cross-cutting patterns: e.g. the same user appearing in both
   low_cpu_efficiency and excessive_evictions, or a node appearing in both
   unhealthy_nodes and the hold_reason_summary.
4. For each flagged item classify it as NEW, ONGOING, or RESOLVED based on
   the prior run context.
5. For unhealthy nodes, assess whether the evidence suggests hardware failure,
   misconfiguration, or a transient problem.

## Output format
Return a JSON object.  All list items in flagged_users and flagged_nodes
must be plain strings (usernames / hostnames only, no nested objects).
Example of correct format: "flagged_users": ["alice", "bob"]
{
  "executive_summary": "<2-3 sentences>",
  "held_or_removed": [...],
  "memory_exceeded": [...],
  "low_cpu_efficiency": [...],
  "excessive_evictions": [...],
  "unhealthy_nodes": [...],
  "cross_cutting_patterns": [...],
  "flagged_users": ["<username>", ...],
  "flagged_nodes": ["<nodename>", ...],
  "new_issues": ["<description>", ...],
  "ongoing_issues": ["<description>", ...],
  "resolved_issues": ["<description>", ...]
}
""")

HYBRID_TEMPLATES["resource_efficiency"] = dedent("""\
You are an HTCondor cluster monitoring analyst.

## Pre-computed findings — Weekly Resource Efficiency Review ({{ now }})
The following metrics cover the last {{ findings.hours_back }} hours.
You do not need to query OpenSearch.

{{ findings_json }}

## Prior run context
{{ prior }}

## Your tasks
1. Write an executive summary focusing on the biggest sources of resource waste.
2. For the top wasters by CPU-hours and memory-GB-hours, explain likely causes
   (e.g. short jobs with long wall time suggest I/O waiting; memory overrequest
   suggests conservative defaults that users haven't tuned).
3. Generate specific RequestMemory and RequestCpus recommendations for the top 5
   over-requesters, based on the suggested_request_mb values in memory_overrequested
   and the p90 CPU values in per_user_stats.
4. Classify each flagged user as NEW, ONGOING, or RESOLVED vs prior runs.

## Output format — flagged_users must be a flat list of plain strings.
{
  "executive_summary": "...",
  "top_cpu_wasters": [...],
  "top_memory_wasters": [...],
  "recommendations": {"<user>": {"RequestMemory": <MB>, "RequestCpus": <n>}, ...},
  "flagged_users": ["<username>", ...],
  "new_issues": ["<description>", ...],
  "ongoing_issues": ["<description>", ...],
  "resolved_issues": ["<description>", ...]
}
""")

HYBRID_TEMPLATES["node_health"] = dedent("""\
You are an HTCondor cluster monitoring analyst.

## Pre-computed findings — Node Health ({{ now }})
The following metrics cover the last {{ findings.hours_back }} hours.
You do not need to query OpenSearch.

{{ findings_json }}

## Prior run context
{{ prior }}

## Your tasks
1. Write an executive summary of node health across the cluster.
2. For each unhealthy node, assess the likely cause from the available evidence:
   - High shadow_exceptions → likely network or authentication issues
   - High failure rate with exit code 1 → likely application misconfiguration
   - High failure rate with signal exits (codes > 128) → likely OOM or preemption
   - High failure rate with varied exit codes → likely hardware instability
3. Recommend a specific action for each flagged node: monitor, drain, or take offline.
4. Classify each flagged node as NEW, ONGOING, or RESOLVED vs prior runs.

## Output format — flagged_nodes must be a flat list of plain strings.
{
  "executive_summary": "...",
  "suspect_nodes": [{"node": "...", "failure_rate_pct": ..., "likely_cause": "...", "recommendation": "..."}, ...],
  "exit_code_analysis": [...],
  "flagged_nodes": ["<nodename>", ...],
  "new_issues": ["<description>", ...],
  "ongoing_issues": ["<description>", ...],
  "resolved_issues": ["<description>", ...]
}
""")

HYBRID_TEMPLATES["anomaly_detection"] = dedent("""\
You are an HTCondor cluster monitoring analyst.

## Pre-computed findings — Anomaly Detection ({{ now }})
The following metrics compare the last {{ findings.hours_back }} hours against
the prior equivalent period.  You do not need to query OpenSearch.

{{ findings_json }}

## Prior run context
{{ prior }}

## Your tasks
1. Write an executive summary of the most significant anomalies.
2. For each anomalous user, assess whether the change is likely intentional
   (e.g. a new large workflow) or a problem (e.g. runaway submission script,
   sudden application failure).
3. For new users, note whether their resource usage patterns look reasonable
   or whether they need guidance.
4. Cross-reference fleet_outliers against anomalous_users — users appearing
   in both are higher priority.
5. Classify each finding as NEW, ONGOING, or RESOLVED vs prior runs.

## Output format — flagged_users must be a flat list of plain strings.
{
  "executive_summary": "...",
  "anomalous_users": [{"user": "...", "anomaly_type": "...", "assessment": "...", "severity": "low|medium|high"}, ...],
  "new_users": [{"user": "...", "assessment": "..."}, ...],
  "fleet_outliers": [...],
  "flagged_users": ["<username>", ...],
  "new_issues": ["<description>", ...],
  "ongoing_issues": ["<description>", ...],
  "resolved_issues": ["<description>", ...]
}
""")

HYBRID_TEMPLATES["user_behavior_trends"] = dedent("""\
You are an HTCondor cluster monitoring analyst.

## Pre-computed findings — User Behaviour Trends ({{ now }}, {{ cadence }})
The following metrics compare the last {{ findings.hours_back }} hours against
the prior equivalent period.  You do not need to query OpenSearch.

{{ findings_json }}

## Prior run context
{{ prior }}

## Your tasks
1. Identify the most significant behavioural shifts across the user base.
2. Distinguish changes that are likely scheduled/intentional from genuine anomalies.
3. Flag any users whose combined efficiency metrics suggest they need guidance
   on resource requesting.
4. Classify each finding as NEW, ONGOING, or RESOLVED vs prior runs.

## Output format — flagged_users must be a flat list of plain strings.
{
  "executive_summary": "...",
  "significant_shifts": [...],
  "users_needing_guidance": [{"user": "...", "reason": "..."}, ...],
  "new_users": [...],
  "flagged_users": ["<username>", ...],
  "new_issues": ["<description>", ...],
  "ongoing_issues": ["<description>", ...],
  "resolved_issues": ["<description>", ...]
}
""")

HYBRID_TEMPLATES["gpu_utilization"] = dedent("""\
You are an HTCondor cluster monitoring analyst.

## Pre-computed findings — GPU Utilisation ({{ now }})
The following metrics cover the last {{ findings.hours_back }} hours.
You do not need to query OpenSearch.

{{ findings_json }}

## Prior run context
{{ prior }}

## Your tasks
1. Identify users who are wasting GPU slots (low CPU efficiency is a proxy
   where dedicated GPU usage metrics are absent).
2. Recommend whether flagged users should switch to CPU-only slots.
3. Note any caveats about the available data (see the "note" field in findings).
4. Classify each finding as NEW, ONGOING, or RESOLVED vs prior runs.

## Output format — flagged_users must be a flat list of plain strings.
{
  "executive_summary": "...",
  "gpu_waste_by_user": [{"user": "...", "assessment": "...", "recommendation": "..."}, ...],
  "flagged_users": ["<username>", ...],
  "new_issues": ["<description>", ...],
  "ongoing_issues": ["<description>", ...],
  "resolved_issues": ["<description>", ...]
}
""")

HYBRID_TEMPLATES["long_running_jobs"] = dedent("""\
You are an HTCondor cluster monitoring analyst.

## Pre-computed findings — Long-Running Jobs ({{ now }})
The following job records have wall times exceeding {{ findings.thresholds.long_job_fallback_hours }} hours.
You do not need to query OpenSearch.

{{ findings_json }}

## Prior run context
{{ prior }}

## Your tasks
For each job in long_running_jobs and long_completed_jobs:
1. Compute CPU efficiency from RemoteUserCpu / RemoteWallClockTime if both are present.
2. Check NumJobStarts — values > 2 indicate evictions.
3. Look for OnExitRemove or CheckpointedAt fields to determine if checkpointing is active.
4. Classify each as: GOOD_USE, STALLED, LEAKING_MEMORY, or NEEDS_REVIEW.
5. Flag any jobs that should be terminated or whose users should be contacted.

## Output format — flagged_users must be a flat list of plain strings.
{
  "executive_summary": "...",
  "classified_jobs": [{"job_id": "...", "user": "...", "wall_hours": ..., "cpu_eff_pct": ..., "classification": "...", "action": "..."}, ...],
  "flagged_users": ["<username>", ...],
  "new_issues": ["<description>", ...],
  "ongoing_issues": ["<description>", ...],
  "resolved_issues": ["<description>", ...]
}
""")


# ── Builder ────────────────────────────────────────────────────────────────────

class PromptBuilder:
    """
    Renders a named prompt template with pre-computed findings and prior context.

    In hybrid mode (default), the caller passes a findings dict from an
    orchestrator function.  The builder serialises it as JSON and injects it
    into the template so the agent receives structured data rather than needing
    to query OpenSearch itself.
    """

    _env = Environment(loader=BaseLoader(), autoescape=False)

    def __init__(self, state_store: StateStore | None = None):
        self._store = state_store or StateStore()

    @property
    def available_tasks(self) -> list[str]:
        return list(HYBRID_TEMPLATES.keys())

    def build(
        self,
        task_name: str,
        findings: dict | None = None,
        cadence: str | None = None,
        extra_vars: dict | None = None,
    ) -> str:
        """
        Render the prompt template for *task_name*.

        Args:
            task_name: One of the keys in HYBRID_TEMPLATES.
            findings: Pre-computed findings dict from an orchestrator function.
                      If None the template receives an empty findings object
                      (useful for dry-run / testing).
            cadence: Override the global settings cadence.
            extra_vars: Additional Jinja2 variables.

        Returns:
            Fully rendered prompt string ready to pass to the agent.
        """
        if task_name not in HYBRID_TEMPLATES:
            raise ValueError(
                f"Unknown task '{task_name}'.  Available: {self.available_tasks}"
            )

        cadence = cadence or settings.cadence
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        period_map = {"daily": "24 hours", "weekly": "7 days", "monthly": "30 days"}
        period_label = period_map.get(cadence, "period")
        prior = self._store.prior_context_block(cadence, task_name)

        findings = findings or {}

        ctx = {
            "cfg":          settings,
            "cadence":      cadence,
            "now":          now,
            "period_label": period_label,
            "prior":        prior,
            "findings":     findings,
            "findings_json": json.dumps(findings, indent=2, default=str),
        }
        if extra_vars:
            ctx.update(extra_vars)

        template = self._env.from_string(HYBRID_TEMPLATES[task_name])
        return template.render(**ctx)
