"""
State management for the monitoring agent.

Persists a rolling window of prior run summaries to disk so each agent
invocation can distinguish new findings from already-known issues, and
track trend changes over time.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .settings import settings

logger = logging.getLogger(__name__)


class RunRecord:
    """Represents one completed monitoring run."""

    def __init__(
        self,
        cadence: str,
        task_name: str,
        run_at: datetime,
        findings_summary: str,
        findings_json: dict[str, Any],
        agent_steps: int = 0,
    ):
        self.cadence = cadence
        self.task_name = task_name
        self.run_at = run_at
        self.findings_summary = findings_summary
        self.findings_json = findings_json
        self.agent_steps = agent_steps

    def to_dict(self) -> dict:
        return {
            "cadence": self.cadence,
            "task_name": self.task_name,
            "run_at": self.run_at.isoformat(),
            "findings_summary": self.findings_summary,
            "findings_json": self.findings_json,
            "agent_steps": self.agent_steps,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RunRecord":
        return cls(
            cadence=d["cadence"],
            task_name=d["task_name"],
            run_at=datetime.fromisoformat(d["run_at"]),
            findings_summary=d["findings_summary"],
            findings_json=d.get("findings_json", {}),
            agent_steps=d.get("agent_steps", 0),
        )

    def short_context(self) -> str:
        """Return a compact string for injection into the next run's prompt."""
        ts = self.run_at.strftime("%Y-%m-%d %H:%M UTC")
        return (
            f"[{ts} — {self.cadence} — {self.task_name}]\n"
            f"{self.findings_summary}\n"
        )


class StateStore:
    """
    Simple JSON-file-backed store for run history.

    One file per (cadence, task_name) combination, e.g.:
      monitor_state/daily__health_check.json
    """

    def __init__(self, state_dir: Path | None = None):
        self.state_dir = state_dir or settings.state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, cadence: str, task_name: str) -> Path:
        slug = f"{cadence}__{task_name}".replace(" ", "_").lower()
        return self.state_dir / f"{slug}.json"

    def load(self, cadence: str, task_name: str) -> list[RunRecord]:
        path = self._path(cadence, task_name)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text())
            return [RunRecord.from_dict(r) for r in data]
        except Exception as exc:
            logger.warning("Failed to load state from %s: %s", path, exc)
            return []

    def save(self, record: RunRecord) -> None:
        history = self.load(record.cadence, record.task_name)
        history.append(record)
        # Keep only the most recent N records
        history = history[-settings.state_history_depth :]
        path = self._path(record.cadence, record.task_name)
        path.write_text(json.dumps([r.to_dict() for r in history], indent=2))
        logger.debug("State saved to %s (%d records)", path, len(history))

    def prior_context_block(self, cadence: str, task_name: str) -> str:
        """
        Return a formatted block of prior run summaries suitable for
        injection into an agent prompt as context.
        """
        history = self.load(cadence, task_name)
        if not history:
            return "No prior runs found for this task — this appears to be the first execution."
        lines = ["## Prior Run Summaries (most recent last)\n"]
        for rec in history:
            lines.append(rec.short_context())
        lines.append(
            "\nWhen reporting findings, explicitly note whether each issue is NEW "
            "(not seen in prior runs), ONGOING (seen before and still present), "
            "or RESOLVED (was flagged before but not seen now).\n"
        )
        return "\n".join(lines)

    def known_issue_keys(self, cadence: str, task_name: str) -> set[str]:
        """
        Return a set of simple string keys from prior findings_json that
        the agent can use to label issues as new vs. ongoing.
        """
        history = self.load(cadence, task_name)
        keys: set[str] = set()
        for rec in history:
            fj = rec.findings_json
            # Convention: findings_json may have lists of flagged users/nodes
            for k in ("flagged_users", "flagged_nodes", "hold_reasons"):
                for item in fj.get(k, []):
                    keys.add(f"{k}:{item}")
        return keys
