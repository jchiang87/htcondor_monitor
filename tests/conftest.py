"""Shared fixtures for htcondor_monitor tests."""

from __future__ import annotations

import warnings
from datetime import datetime, timezone

import pytest

from htcondor_monitor.store import RunRecord, StateStore

# Suppress the "no .env file found" warning that fires when Settings() is
# instantiated in any module under test.
warnings.filterwarnings("ignore", category=UserWarning)


@pytest.fixture
def sample_record() -> RunRecord:
    return RunRecord(
        cadence="daily",
        task_name="health_check",
        run_at=datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc),
        findings_summary="All systems nominal. No critical issues found.",
        findings_json={
            "executive_summary": "All systems nominal. No critical issues found.",
            "new_issues": ["high memory usage for user alice"],
            "ongoing_issues": ["low cpu efficiency for user bob"],
            "resolved_issues": [],
            "flagged_users": ["alice", "bob"],
            "flagged_nodes": ["node01"],
        },
        agent_steps=5,
    )


@pytest.fixture
def store(tmp_path) -> StateStore:
    return StateStore(state_dir=tmp_path)
