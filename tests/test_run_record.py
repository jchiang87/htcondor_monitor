"""Tests for RunRecord serialization and formatting."""

from __future__ import annotations

from datetime import datetime, timezone

from htcondor_monitor.store import RunRecord


def _make_record(**kwargs) -> RunRecord:
    defaults = dict(
        cadence="daily",
        task_name="health_check",
        run_at=datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc),
        findings_summary="Test summary.",
        findings_json={"executive_summary": "Test summary."},
        agent_steps=3,
    )
    defaults.update(kwargs)
    return RunRecord(**defaults)


def test_to_dict_contains_all_fields():
    record = _make_record()
    d = record.to_dict()
    assert set(d.keys()) == {
        "cadence", "task_name", "run_at",
        "findings_summary", "findings_json", "agent_steps",
    }


def test_from_dict_round_trip(sample_record):
    restored = RunRecord.from_dict(sample_record.to_dict())
    assert restored.cadence == sample_record.cadence
    assert restored.task_name == sample_record.task_name
    assert restored.run_at == sample_record.run_at
    assert restored.findings_summary == sample_record.findings_summary
    assert restored.findings_json == sample_record.findings_json
    assert restored.agent_steps == sample_record.agent_steps


def test_from_dict_missing_agent_steps_defaults_to_zero():
    d = _make_record().to_dict()
    del d["agent_steps"]
    restored = RunRecord.from_dict(d)
    assert restored.agent_steps == 0


def test_from_dict_missing_findings_json_defaults_to_empty():
    d = _make_record().to_dict()
    del d["findings_json"]
    restored = RunRecord.from_dict(d)
    assert restored.findings_json == {}


def test_short_context_contains_timestamp():
    record = _make_record(run_at=datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc))
    ctx = record.short_context()
    assert "2026-05-20 12:00 UTC" in ctx


def test_short_context_contains_cadence_and_task_name():
    record = _make_record(cadence="weekly", task_name="resource_efficiency")
    ctx = record.short_context()
    assert "weekly" in ctx
    assert "resource_efficiency" in ctx


def test_short_context_contains_findings_summary():
    record = _make_record(findings_summary="Unique summary text XYZ.")
    ctx = record.short_context()
    assert "Unique summary text XYZ." in ctx


def test_to_dict_run_at_is_isoformat_string():
    record = _make_record(run_at=datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc))
    d = record.to_dict()
    assert isinstance(d["run_at"], str)
    # Must be parseable back to datetime
    parsed = datetime.fromisoformat(d["run_at"])
    assert parsed == record.run_at


def test_from_dict_preserves_timezone_aware_datetime():
    record = _make_record(run_at=datetime(2026, 5, 20, 9, 30, 0, tzinfo=timezone.utc))
    restored = RunRecord.from_dict(record.to_dict())
    assert restored.run_at.tzinfo is not None
    assert restored.run_at == record.run_at
