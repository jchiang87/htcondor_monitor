"""Tests for MonitoringAgent._extract_json — pure static method, zero mocks."""

from __future__ import annotations

import pytest

from htcondor_monitor.monitoring_agent import MonitoringAgent

extract = MonitoringAgent._extract_json


# ── Happy path ────────────────────────────────────────────────────────────────

def test_plain_json_object():
    result = extract('{"key": "value"}')
    assert result == {"key": "value"}


def test_json_with_multiple_fields():
    result = extract('{"a": 1, "b": "hello", "c": true}')
    assert result == {"a": 1, "b": "hello", "c": True}


def test_json_in_json_fence():
    raw = '```json\n{"executive_summary": "All good."}\n```'
    result = extract(raw)
    assert result == {"executive_summary": "All good."}


def test_json_in_plain_fence():
    raw = '```\n{"executive_summary": "Works too."}\n```'
    result = extract(raw)
    assert result == {"executive_summary": "Works too."}


def test_json_embedded_in_prose():
    raw = 'Here are the findings: {"status": "ok", "count": 5} End of report.'
    result = extract(raw)
    assert result == {"status": "ok", "count": 5}


def test_json_with_leading_prose():
    raw = 'After careful analysis:\n\n{"result": "clean"}'
    result = extract(raw)
    assert result == {"result": "clean"}


def test_nested_json_preserved():
    raw = '{"outer": {"inner": [1, 2, 3]}, "flag": false}'
    result = extract(raw)
    assert result == {"outer": {"inner": [1, 2, 3]}, "flag": False}


def test_multiline_json_in_fence():
    raw = (
        "```json\n"
        "{\n"
        '  "executive_summary": "Multi-line JSON.",\n'
        '  "new_issues": ["issue1", "issue2"],\n'
        '  "flagged_users": ["alice"]\n'
        "}\n"
        "```"
    )
    result = extract(raw)
    assert result["executive_summary"] == "Multi-line JSON."
    assert result["new_issues"] == ["issue1", "issue2"]
    assert result["flagged_users"] == ["alice"]


# ── Dict passthrough ──────────────────────────────────────────────────────────

def test_input_is_already_dict():
    d = {"key": "value", "number": 42}
    result = extract(d)
    assert result is d


def test_input_is_empty_dict():
    result = extract({})
    assert result == {}


# ── Error / fallback paths ────────────────────────────────────────────────────

def test_no_braces_returns_parse_error():
    result = extract("No JSON here at all.")
    assert result.get("_parse_error") is True


def test_empty_string_returns_parse_error():
    result = extract("")
    assert result.get("_parse_error") is True


def test_malformed_json_returns_parse_error():
    result = extract("{invalid json here}")
    assert result.get("_parse_error") is True


def test_parse_error_puts_raw_in_executive_summary():
    raw = "Some plain text with no JSON."
    result = extract(raw)
    assert result["executive_summary"] == raw


def test_malformed_json_puts_raw_in_executive_summary():
    raw = "{not: valid}"
    result = extract(raw)
    assert result["executive_summary"] == raw


def test_partial_json_braces_but_invalid():
    result = extract("{ unclosed")
    assert result.get("_parse_error") is True


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_json_with_unicode():
    raw = '{"summary": "Résumé avec des caractères spéciaux 日本語"}'
    result = extract(raw)
    assert "Résumé" in result["summary"]


def test_json_array_inside_object():
    raw = '{"items": [1, 2, 3], "flagged_users": ["a", "b"]}'
    result = extract(raw)
    assert result["items"] == [1, 2, 3]
    assert result["flagged_users"] == ["a", "b"]
