"""
MonitoringAgent — wires together orchestrator → prompt → CodeAgent → state.

Flow:
  1. Orchestrator runs deterministic queries + metrics (OpenSearch, pure Python)
  2. PromptBuilder renders a hybrid prompt with pre-computed findings injected
  3. CodeAgent synthesises a narrative report and classifies new/ongoing/resolved
  4. StateStore persists the findings for continuity in the next run
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from smolagents import CodeAgent, OpenAIModel
from smolagents.agents import AgentMaxStepsError

from .settings import settings
from .builder import PromptBuilder
from .store import StateStore, RunRecord
from .tools.opensearch_tools import ALL_TOOLS
from .orchestrators import run_orchestrator, ORCHESTRATORS

logger = logging.getLogger(__name__)


class MonitoringAgent:
    """
    High-level agent runner for a single monitoring task.

    Typical usage::

        agent = MonitoringAgent()
        record = agent.run("health_check")
        print(record.findings_json["executive_summary"])
    """

    def __init__(
        self,
        state_store: StateStore | None = None,
        prompt_builder: PromptBuilder | None = None,
        extra_tools: list | None = None,
    ):
        self._store = state_store or StateStore()
        self._builder = prompt_builder or PromptBuilder(self._store)
        self._extra_tools = extra_tools or []

    def _build_llm(self) -> OpenAIModel:
        api_key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "No Anthropic API key found.  Set HTCONDOR_ANTHROPIC_API_KEY "
                "or ANTHROPIC_API_KEY environment variable."
            )
        api_base = settings.anthropic_base_url or os.environ.get("ANTHROPIC_BASE_URL")
        if not api_base:
            raise RuntimeError(
                "No Anthropic base url found.  Set ANTHROPIC_BASE_URL in .env "
                "or ANTHROPIC_BASE_URL environment variable."
            )
        return OpenAIModel(
            model_id=settings.anthropic_model,
            api_key=api_key,
            api_base=api_base,
        )

    def _build_agent(self) -> CodeAgent:
        return CodeAgent(
            tools=ALL_TOOLS + self._extra_tools,
            model=self._build_llm(),
            max_steps=settings.agent_max_steps,
            verbosity_level=settings.agent_verbosity,
            additional_authorized_imports=["json", "calendar"],
        )

    @staticmethod
    def _extract_findings(raw_output: Any) -> dict[str, Any]:
        """
        Normalise agent output to a findings dict.
        smolagents may return a dict directly if the model output was valid JSON.
        """
        if isinstance(raw_output, dict):
            return raw_output
        if not isinstance(raw_output, str):
            logger.warning("Unexpected agent output type %s", type(raw_output))
            raw_output = str(raw_output)
        # Plain text fallback — store as executive summary
        return {"executive_summary": raw_output, "_parse_error": True}

    @staticmethod
    def _normalise_findings(fj: dict[str, Any]) -> dict[str, Any]:
        """
        Coerce known fields to their expected types in one place so that
        reporting and state code don't need per-field isinstance checks.
        """
        def _to_str(v: Any) -> str:
            if isinstance(v, dict):
                return v.get("user") or v.get("node") or v.get("name") or str(v)
            return str(v)

        for key in ("flagged_users", "flagged_nodes"):
            if key in fj and isinstance(fj[key], list):
                fj[key] = [_to_str(v) for v in fj[key]]

        for key in ("new_issues", "ongoing_issues", "resolved_issues"):
            if key in fj and isinstance(fj[key], list):
                fj[key] = [str(v) if not isinstance(v, str) else v for v in fj[key]]

        return fj

    def _recover_partial_output(self, agent: CodeAgent) -> dict[str, Any]:
        """Extract whatever the agent produced before hitting max_steps."""
        parts = []
        for step in getattr(agent, "logs", []):
            obs = getattr(step, "observations", None)
            if obs:
                parts.append(str(obs))
        text = "\n".join(parts) if parts else "Agent reached max_steps with no recoverable output."
        return {
            "executive_summary": f"[INCOMPLETE — hit max_steps limit] {text[:500]}",
            "_max_steps_reached": True,
        }

    def run(
        self,
        task_name: str,
        cadence: str | None = None,
        dry_run: bool = False,
        hours_back: int | None = None,
        extra_prompt_vars: dict | None = None,
    ) -> RunRecord:
        """
        Execute a monitoring task and persist the result.

        Args:
            task_name: Name of the task to run.
            cadence: Override global cadence setting.
            dry_run: Print rendered prompt without calling LLM or OpenSearch.
            hours_back: Override the default lookback window for this task.
            extra_prompt_vars: Extra Jinja2 variables forwarded to the prompt builder.

        Returns:
            A RunRecord containing the findings, persisted to StateStore.
        """
        cadence = cadence or settings.cadence
        run_at = datetime.now(timezone.utc)

        # ── Step 1: pre-compute findings via orchestrator ──────────────────
        if dry_run:
            findings: dict[str, Any] = {"_dry_run": True}
        else:
            logger.info("Running orchestrator for task=%s", task_name)
            try:
                findings = run_orchestrator(task_name, hours_back=hours_back)
            except Exception as exc:
                logger.error("Orchestrator failed for task=%s: %s", task_name, exc)
                raise

        # ── Step 2: render prompt with findings injected ───────────────────
        prompt = self._builder.build(
            task_name=task_name,
            findings=findings,
            cadence=cadence,
            extra_vars=extra_prompt_vars,
        )

        if dry_run:
            print("=" * 72)
            print(f"DRY RUN — task={task_name}  cadence={cadence}")
            print("=" * 72)
            print(prompt)
            print("=" * 72)
            return RunRecord(
                cadence=cadence,
                task_name=task_name,
                run_at=run_at,
                findings_summary="[dry run — no LLM call made]",
                findings_json={},
            )

        # ── Step 3: call the agent for synthesis ───────────────────────────
        logger.info("Starting agent synthesis for task=%s cadence=%s", task_name, cadence)
        agent = self._build_agent()

        try:
            raw_output = agent.run(prompt)
        except AgentMaxStepsError:
            logger.warning(
                "Agent hit max_steps (%d) for task=%s — capturing partial output",
                settings.agent_max_steps, task_name,
            )
            raw_output = self._recover_partial_output(agent)

        # ── Step 4: normalise and persist ──────────────────────────────────
        findings_json = self._normalise_findings(self._extract_findings(raw_output))
        summary = findings_json.get(
            "executive_summary",
            str(raw_output)[:500],
        )

        record = RunRecord(
            cadence=cadence,
            task_name=task_name,
            run_at=run_at,
            findings_summary=summary,
            findings_json=findings_json,
            agent_steps=getattr(agent, "step_number", 0),
        )
        self._store.save(record)
        logger.info("Task %s complete. Summary: %s", task_name, summary[:120])
        return record
