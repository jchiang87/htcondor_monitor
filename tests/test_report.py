"""Tests for report formatting functions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from htcondor_monitor.store import RunRecord
import htcondor_monitor.report as report_module
from htcondor_monitor.report import _build_html, save_json_report, send_email_report


@pytest.fixture
def full_record() -> RunRecord:
    return RunRecord(
        cadence="daily",
        task_name="health_check",
        run_at=datetime(2026, 5, 20, 14, 30, 0, tzinfo=timezone.utc),
        findings_summary="Two new issues detected.",
        findings_json={
            "executive_summary": "Two new issues detected.",
            "new_issues": ["high memory: user alice", "stalled job: 12345.0"],
            "ongoing_issues": ["low efficiency: user bob"],
            "resolved_issues": ["old issue now gone"],
            "flagged_users": ["alice", "bob"],
            "flagged_nodes": ["node01"],
            "recommendations": {
                "alice": {"RequestMemory": 4096, "RequestCpus": 2},
            },
        },
        agent_steps=7,
    )


@pytest.fixture
def empty_record() -> RunRecord:
    return RunRecord(
        cadence="weekly",
        task_name="resource_efficiency",
        run_at=datetime(2026, 5, 20, 8, 0, 0, tzinfo=timezone.utc),
        findings_summary="",
        findings_json={},
        agent_steps=0,
    )


# ── _build_html: pure HTML generation ────────────────────────────────────────

def test_build_html_is_valid_envelope(full_record):
    html = _build_html(full_record)
    assert html.startswith("<html>")
    assert html.endswith("</html>")


def test_build_html_contains_task_name(full_record):
    html = _build_html(full_record)
    assert "Health Check" in html  # title-cased


def test_build_html_contains_executive_summary(full_record):
    html = _build_html(full_record)
    assert "Two new issues detected." in html


def test_build_html_contains_timestamp(full_record):
    html = _build_html(full_record)
    assert "2026-05-20 14:30 UTC" in html


def test_build_html_new_issues_uses_red(full_record):
    html = _build_html(full_record)
    assert "#c0392b" in html
    assert "high memory: user alice" in html


def test_build_html_ongoing_issues_uses_orange(full_record):
    html = _build_html(full_record)
    assert "#e67e22" in html
    assert "low efficiency: user bob" in html


def test_build_html_resolved_issues_uses_green(full_record):
    html = _build_html(full_record)
    assert "#27ae60" in html
    assert "old issue now gone" in html


def test_build_html_flagged_users_present(full_record):
    html = _build_html(full_record)
    assert "alice" in html
    assert "bob" in html


def test_build_html_flagged_nodes_present(full_record):
    html = _build_html(full_record)
    assert "node01" in html


def test_build_html_recommendations_table(full_record):
    html = _build_html(full_record)
    assert "<table" in html
    assert "4096" in html
    assert "alice" in html


def test_build_html_empty_findings_no_crash(empty_record):
    html = _build_html(empty_record)
    assert "<html>" in html


def test_build_html_no_recommendations_no_table(empty_record):
    html = _build_html(empty_record)
    assert "<table" not in html


# ── save_json_report ──────────────────────────────────────────────────────────

def test_save_json_creates_file(tmp_path, full_record, monkeypatch):
    monkeypatch.setattr(report_module.settings, "report_output_dir", tmp_path)
    path = save_json_report(full_record)
    assert path.exists()


def test_save_json_returns_path_object(tmp_path, full_record, monkeypatch):
    monkeypatch.setattr(report_module.settings, "report_output_dir", tmp_path)
    path = save_json_report(full_record)
    assert isinstance(path, Path)


def test_save_json_filename_format(tmp_path, full_record, monkeypatch):
    monkeypatch.setattr(report_module.settings, "report_output_dir", tmp_path)
    path = save_json_report(full_record)
    # daily__health_check__20260520_1430.json
    assert path.name.startswith("daily__health_check__")
    assert path.suffix == ".json"


def test_save_json_contents_are_valid_json(tmp_path, full_record, monkeypatch):
    monkeypatch.setattr(report_module.settings, "report_output_dir", tmp_path)
    path = save_json_report(full_record)
    data = json.loads(path.read_text())
    assert isinstance(data, dict)


def test_save_json_contents_match_record(tmp_path, full_record, monkeypatch):
    monkeypatch.setattr(report_module.settings, "report_output_dir", tmp_path)
    path = save_json_report(full_record)
    data = json.loads(path.read_text())
    assert data["cadence"] == "daily"
    assert data["task_name"] == "health_check"
    assert data["findings_json"]["flagged_users"] == ["alice", "bob"]


def test_save_json_creates_output_dir_if_missing(tmp_path, full_record, monkeypatch):
    nested = tmp_path / "new" / "output"
    monkeypatch.setattr(report_module.settings, "report_output_dir", nested)
    save_json_report(full_record)
    assert nested.exists()


# ── send_email_report ─────────────────────────────────────────────────────────

def test_send_email_returns_false_when_no_recipients(full_record, monkeypatch):
    monkeypatch.setattr(report_module.settings, "report_email_to", "")
    result = send_email_report(full_record)
    assert result is False


def test_send_email_returns_false_for_whitespace_only_recipients(full_record, monkeypatch):
    monkeypatch.setattr(report_module.settings, "report_email_to", "  ,  ,  ")
    result = send_email_report(full_record)
    assert result is False


# ── print_report: smoke test ──────────────────────────────────────────────────

def test_print_report_no_crash(full_record, capsys):
    report_module.print_report(full_record)
    # Rich writes to its own console; just verify no exception is raised.


def test_print_report_empty_findings_no_crash(empty_record):
    report_module.print_report(empty_record)
