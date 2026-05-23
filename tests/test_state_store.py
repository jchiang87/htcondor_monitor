"""Tests for StateStore file I/O logic using real temp directories."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from htcondor_monitor.store import RunRecord, StateStore


def _make_record(summary: str = "Test summary.", index: int = 0) -> RunRecord:
    return RunRecord(
        cadence="daily",
        task_name="health_check",
        run_at=datetime(2026, 5, 20, 12, index, 0, tzinfo=timezone.utc),
        findings_summary=summary,
        findings_json={
            "executive_summary": summary,
            "flagged_users": ["alice"],
            "flagged_nodes": ["node01"],
        },
        agent_steps=1,
    )


def test_save_and_load_single_record(store, sample_record):
    store.save(sample_record)
    records = store.load("daily", "health_check")
    assert len(records) == 1
    assert records[0].task_name == "health_check"
    assert records[0].findings_summary == sample_record.findings_summary


def test_save_multiple_records_accumulate(store):
    for i in range(3):
        store.save(_make_record(f"summary {i}", index=i))
    records = store.load("daily", "health_check")
    assert len(records) == 3


def test_save_trims_to_history_depth(store):
    from htcondor_monitor.settings import settings
    depth = settings.state_history_depth  # default 5
    for i in range(depth + 3):
        store.save(_make_record(f"run {i}", index=i % 60))
    records = store.load("daily", "health_check")
    assert len(records) == depth


def test_save_keeps_most_recent_records(store):
    from htcondor_monitor.settings import settings
    depth = settings.state_history_depth
    for i in range(depth + 2):
        store.save(_make_record(f"run {i}", index=i % 60))
    records = store.load("daily", "health_check")
    summaries = [r.findings_summary for r in records]
    # Oldest records should have been dropped
    assert "run 0" not in summaries
    assert f"run {depth + 1}" in summaries


def test_load_returns_empty_list_for_missing_file(store):
    records = store.load("daily", "nonexistent_task")
    assert records == []


def test_load_returns_empty_list_for_corrupt_json(store, tmp_path):
    path = tmp_path / "daily__health_check.json"
    path.write_text("not valid json {{{")
    records = store.load("daily", "health_check")
    assert records == []


def test_prior_context_block_no_history(store):
    block = store.prior_context_block("daily", "health_check")
    assert "No prior runs" in block


def test_prior_context_block_includes_summaries(store):
    store.save(_make_record("First run summary.", index=0))
    store.save(_make_record("Second run summary.", index=1))
    block = store.prior_context_block("daily", "health_check")
    assert "First run summary." in block
    assert "Second run summary." in block


def test_prior_context_block_includes_new_ongoing_resolved_instructions(store, sample_record):
    store.save(sample_record)
    block = store.prior_context_block("daily", "health_check")
    assert "NEW" in block
    assert "ONGOING" in block
    assert "RESOLVED" in block


def test_prior_context_block_has_header(store, sample_record):
    store.save(sample_record)
    block = store.prior_context_block("daily", "health_check")
    assert "Prior Run Summaries" in block


def test_known_issue_keys_empty_for_no_history(store):
    keys = store.known_issue_keys("daily", "health_check")
    assert keys == set()


def test_known_issue_keys_extracts_flagged_users(store):
    record = RunRecord(
        cadence="daily",
        task_name="health_check",
        run_at=datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc),
        findings_summary="s",
        findings_json={"flagged_users": ["alice", "bob"], "flagged_nodes": []},
    )
    store.save(record)
    keys = store.known_issue_keys("daily", "health_check")
    assert "flagged_users:alice" in keys
    assert "flagged_users:bob" in keys


def test_known_issue_keys_extracts_flagged_nodes(store):
    record = RunRecord(
        cadence="daily",
        task_name="health_check",
        run_at=datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc),
        findings_summary="s",
        findings_json={"flagged_users": [], "flagged_nodes": ["node01", "node02"]},
    )
    store.save(record)
    keys = store.known_issue_keys("daily", "health_check")
    assert "flagged_nodes:node01" in keys
    assert "flagged_nodes:node02" in keys


def test_known_issue_keys_extracts_hold_reasons(store):
    record = RunRecord(
        cadence="daily",
        task_name="health_check",
        run_at=datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc),
        findings_summary="s",
        findings_json={"hold_reasons": ["Error in input file", "Memory exceeded"]},
    )
    store.save(record)
    keys = store.known_issue_keys("daily", "health_check")
    assert "hold_reasons:Error in input file" in keys


def test_state_file_slug_format(tmp_path):
    store = StateStore(state_dir=tmp_path)
    path = store._path("daily", "health_check")
    assert path.name == "daily__health_check.json"
    assert path.parent == tmp_path


def test_state_file_replaces_spaces_with_underscores(tmp_path):
    store = StateStore(state_dir=tmp_path)
    path = store._path("daily", "my task name")
    assert " " not in path.name


def test_save_creates_state_dir_if_missing(tmp_path):
    nested = tmp_path / "new" / "nested" / "dir"
    store = StateStore(state_dir=nested)
    assert nested.exists()


def test_records_isolated_by_cadence_and_task(store):
    r1 = RunRecord(
        cadence="daily", task_name="health_check",
        run_at=datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc),
        findings_summary="daily health", findings_json={},
    )
    r2 = RunRecord(
        cadence="weekly", task_name="resource_efficiency",
        run_at=datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc),
        findings_summary="weekly efficiency", findings_json={},
    )
    store.save(r1)
    store.save(r2)
    daily = store.load("daily", "health_check")
    weekly = store.load("weekly", "resource_efficiency")
    assert len(daily) == 1
    assert daily[0].findings_summary == "daily health"
    assert len(weekly) == 1
    assert weekly[0].findings_summary == "weekly efficiency"
