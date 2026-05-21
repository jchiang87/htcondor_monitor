"""
MonitoringAgent — thin wrapper around smolagents.CodeAgent that wires together:
  - OpenSearch tools
  - PromptBuilder (templated prompts + prior context)
  - StateStore (persistence of findings)
  - Structured JSON output extraction
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from smolagents import CodeAgent, AnthropicModel

from htcondor_monitor.config.settings import settings
from htcondor_monitor.prompts.builder import PromptBuilder
from htcondor_monitor.state.store import StateStore, RunRecord
from htcondor_monitor.tools.opensearch_tools import ALL_TOOLS

logger = logging.getLogger(__name__)


class MonitoringAgent:
    """
    High-level agent runner for a single monitoring task.

    Typical usage::

        agent = MonitoringAgent()
        result = agent.run("health_check")
        print(result.findings_json["executive_summary"])
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

    def _build_llm(self) -> AnthropicModel:
        api_key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "No Anthropic API key found.  Set HTCONDOR_ANTHROPIC_API_KEY "
                "or ANTHROPIC_API_KEY environment variable."
            )
        return AnthropicModel(
            model_id=settings.anthropic_model,
            api_key=api_key,
        )

    def _build_agent(self) -> CodeAgent:
        return CodeAgent(
            tools=ALL_TOOLS + self._extra_tools,
            model=self._build_llm(),
            max_steps=settings.agent_max_steps,
            verbosity_level=settings.agent_verbosity,
        )

    @staticmethod
    def _extract_json(raw_output: str) -> dict[str, Any]:
        """
        Pull the first JSON object out of the agent's raw text output.
        The agent is instructed to return JSON but may wrap it in prose or
        markdown fences.
        """
        # Try to find a JSON block inside ```json ... ``` fences
        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_output, re.DOTALL)
        if fence_match:
            candidate = fence_match.group(1)
        else:
            # Find the outermost { ... } span
            start = raw_output.find("{")
            end = raw_output.rfind("}")
            if start == -1 or end == -1:
                logger.warning("No JSON object found in agent output; returning empty dict")
                return {"executive_summary": raw_output, "_parse_error": True}
            candidate = raw_output[start : end + 1]

        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            logger.warning("JSON parse failed (%s); returning raw summary", exc)
            return {"executive_summary": raw_output, "_parse_error": True}

    def run(
        self,
        task_name: str,
        cadence: str | None = None,
        dry_run: bool = False,
        extra_prompt_vars: dict | None = None,
    ) -> RunRecord:
        """
        Execute a monitoring task and persist the result.

        Args:
            task_name: Name of the prompt template / task to run.
            cadence: Override global cadence setting.
            dry_run: If True, print the rendered prompt but do not call the LLM.
            extra_prompt_vars: Extra Jinja2 variables forwarded to the prompt builder.

        Returns:
            A RunRecord containing the findings, persisted to StateStore.
        """
        cadence = cadence or settings.cadence
        run_at = datetime.now(timezone.utc)

        prompt = self._builder.build(
            task_name=task_name,
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

        logger.info("Starting agent task=%s cadence=%s", task_name, cadence)
        agent = self._build_agent()

        raw_output: str = agent.run(prompt)

        findings_json = self._extract_json(raw_output)
        summary = findings_json.get(
            "executive_summary",
            raw_output[:500] if isinstance(raw_output, str) else str(raw_output)[:500],
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
        logger.info("Task %s complete.  Summary: %s", task_name, summary[:120])
        return record
