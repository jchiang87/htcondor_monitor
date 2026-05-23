"""Tests for Click CLI commands using CliRunner."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from htcondor_monitor.run import cli
from htcondor_monitor.settings import settings as app_settings


EXPECTED_TASKS = [
    "health_check",
    "resource_efficiency",
    "node_health",
    "long_running_jobs",
    "user_behavior_trends",
    "gpu_utilization",
    "anomaly_detection",
]


@pytest.fixture(autouse=True)
def isolate_state_dir(tmp_path, monkeypatch):
    """Redirect state dir to tmp_path so CLI commands don't touch the real state."""
    monkeypatch.setattr(app_settings, "state_dir", tmp_path)


# ── `tasks` command ───────────────────────────────────────────────────────────

def test_tasks_command_exit_zero():
    runner = CliRunner()
    result = runner.invoke(cli, ["tasks"])
    assert result.exit_code == 0, result.output


def test_tasks_command_lists_all_tasks():
    runner = CliRunner()
    result = runner.invoke(cli, ["tasks"])
    for task in EXPECTED_TASKS:
        assert task in result.output, f"Task '{task}' not found in output"


def test_tasks_command_produces_output():
    runner = CliRunner()
    result = runner.invoke(cli, ["tasks"])
    assert len(result.output) > 0


# ── `print-prompt` command ────────────────────────────────────────────────────

def test_print_prompt_health_check_exit_zero():
    runner = CliRunner()
    result = runner.invoke(cli, ["print-prompt", "health_check"])
    assert result.exit_code == 0, result.output


def test_print_prompt_health_check_contains_analyst_role():
    runner = CliRunner()
    result = runner.invoke(cli, ["print-prompt", "health_check"])
    assert "HTCondor cluster monitoring analyst" in result.output


def test_print_prompt_produces_substantial_output():
    runner = CliRunner()
    result = runner.invoke(cli, ["print-prompt", "health_check"])
    assert len(result.output) > 200


def test_print_prompt_unknown_task_exits_nonzero():
    runner = CliRunner()
    result = runner.invoke(cli, ["print-prompt", "nonexistent_task"])
    assert result.exit_code != 0


@pytest.mark.parametrize("task", EXPECTED_TASKS)
def test_print_prompt_all_tasks_succeed(task):
    runner = CliRunner()
    result = runner.invoke(cli, ["print-prompt", task])
    assert result.exit_code == 0, f"Task '{task}' failed: {result.output}"


# ── `history` command ─────────────────────────────────────────────────────────

def test_history_command_no_records_exits_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "state_dir", tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["history", "health_check"])
    assert result.exit_code == 0


def test_history_command_no_records_prints_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "state_dir", tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["history", "health_check"])
    assert "No history" in result.output or "no history" in result.output.lower()
