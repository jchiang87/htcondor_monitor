# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

`htcondor_monitor` is an agentic HTCondor cluster monitoring system. It uses a [smolagents](https://github.com/huggingface/smolagents) `CodeAgent` backed by Anthropic Claude to query an OpenSearch index of HTCondor job ClassAds and produce structured reports on cluster health, resource waste, anomalies, and node failures. State is persisted across runs so findings can be labeled NEW/ONGOING/RESOLVED.

## Build

This project uses the **LSST SCons/EUPS** build system:

```bash
scons          # builds bin/htcondor-monitor from bin.src/
```

The SCons build uses `lsst.sconsUtils` (must be set up via EUPS). The `ups/htcondor_monitor.table` file handles PATH and PYTHONPATH when the package is set up with EUPS.

## Tests

```bash
pytest                          # run all tests
pytest tests/test_metrics.py    # run a single test file
pytest -k test_run_health_check # run tests matching a pattern
```

Tests mock the OpenSearch query layer (`opensearch_queries`) so no live cluster is needed. The real metrics and orchestrator logic runs against mock data.

## Configuration

All settings load from `~/.config/htcondor_monitor/.env` (not the repo root). Copy `examples/.env.example` there and set permissions with `chmod 600`. All variables use the `HTCONDOR_` prefix (except `ANTHROPIC_API_KEY`).

The critical variables:
- `ANTHROPIC_API_KEY` — Anthropic API key
- `ANTHROPIC_BASE_URL` — API endpoint base URL (required by `OpenAIModel`)
- `HTCONDOR_OPENSEARCH_HOST` — where the HTCondor ClassAd index lives

ClassAd field names are fully configurable via `FIELD_*` env vars (e.g., `HTCONDOR_FIELD_USER`), since different HTCondor deployments use different attribute names.

## Running

After `scons`, the CLI entry point is `bin/htcondor-monitor`:

```bash
htcondor-monitor tasks                                      # list available monitoring tasks
htcondor-monitor print-prompt TASK_NAME                     # preview rendered prompt (no LLM call)
htcondor-monitor run TASK_NAME [--dry-run] [--save-json]    # run a task
htcondor-monitor history TASK_NAME                          # show prior run records
```

The `--dry-run` flag skips both the orchestrator queries and the LLM call, rendering the prompt with empty findings.

Cron scripts in `cron/` are thin wrappers — each instantiates `MonitoringAgent`, calls `.run()`, and calls the three reporting methods.

## Architecture

```
cron/{daily,weekly,monthly}_*.py
        │
        ▼
MonitoringAgent.run(task_name, cadence)           monitoring_agent.py
  │
  ├─ Step 1: run_orchestrator(task_name)          orchestrators.py
  │    ├─ tools/opensearch_queries.py             low-level OpenSearch DSL queries
  │    └─ tools/metrics.py                        pure-Python threshold analysis
  │         └─ returns FindingsContext dict
  │
  ├─ Step 2: PromptBuilder.build(findings)        builder.py
  │    ├─ HYBRID_TEMPLATES[task_name] (Jinja2)
  │    ├─ findings serialised as JSON
  │    └─ StateStore.prior_context_block()        store.py
  │
  ├─ Step 3: CodeAgent.run(prompt)                smolagents
  │    ├─ OpenAIModel("claude-sonnet-...")
  │    └─ ALL_TOOLS (opensearch_tools.py)         smolagents @tool wrappers (fallback)
  │
  └─ Step 4: RunRecord → StateStore.save()
        └─ print_report / save_json / email       report.py
```

**Key design decisions:**
- **Hybrid mode** (default): orchestrators pre-compute all metrics via deterministic Python before the LLM is called. The agent's role is narrative synthesis, cross-signal reasoning, and NEW/ONGOING/RESOLVED classification — typically 2-5 steps. This reduces token usage and makes results reproducible.
- `tools/opensearch_queries.py` contains bare query functions called by orchestrators. `opensearch_tools.py` wraps a subset of them as smolagents `@tool` decorators, available to the agent as a fallback for direct investigation.
- `PromptBuilder` injects prior run summaries so the LLM can distinguish new from ongoing issues without any special logic — it's all done via prompt context.
- `StateStore` writes JSON files to `monitor_state/{cadence}__{task}.json`, keeping only the last `STATE_HISTORY_DEPTH` records.
- `ContextTooLargeError` is raised before the LLM call if findings exceed `HTCONDOR_MAX_FINDINGS_TOKENS` (~4 chars/token heuristic).

## Monitoring Tasks (7 total)

Defined as Jinja2 template strings in `builder.py::HYBRID_TEMPLATES`:

| Task | Default lookback |
|------|----------------|
| `health_check` | 24 h |
| `anomaly_detection` | 24 h |
| `long_running_jobs` | 168 h |
| `resource_efficiency` | 168 h |
| `node_health` | 168 h |
| `gpu_utilization` | 168 h |
| `user_behavior_trends` | 168 h |

**To add a new task**: add an entry to `HYBRID_TEMPLATES` in `builder.py`, add a corresponding function in `orchestrators.py`, register it in `ORCHESTRATORS`, and optionally add a cron script. The CLI picks it up automatically.

## Layer summary

| File | Role |
|------|------|
| `tools/opensearch_queries.py` | Raw OpenSearch DSL — `fetch_jobs`, `fetch_user_aggregations`, `fetch_node_aggregations`, `fetch_hold_reasons`, `fetch_fleet_percentiles` |
| `tools/metrics.py` | Pure-Python threshold analysis — `find_low_cpu_efficiency`, `find_unhealthy_nodes`, `find_anomalous_users`, `rank_by_wasted_cpu_hours`, etc. |
| `tools/opensearch_tools.py` | smolagents `@tool` wrappers around query functions, exported as `ALL_TOOLS` passed to `CodeAgent` |
| `orchestrators.py` | One function per task; calls query + metrics layers; returns `FindingsContext` dict; validates context size via `_check_context_size`. Also contains `classify_long_running_jobs`, which pre-buckets jobs into `leaking_memory`, `terminated`, `eviction_issues`, `stalled`, `good_use`, `needs_review`. The `ORCHESTRATORS` registry maps task names to `(fn, default_hours_back)` tuples — the CLI discovers tasks from this dict. |
| `builder.py` | Renders Jinja2 prompt templates (`HYBRID_TEMPLATES`) with findings + prior context |
| `monitoring_agent.py` | Wires orchestrator → builder → CodeAgent → StateStore |
| `store.py` | `RunRecord` dataclass + `StateStore` JSON persistence |
| `report.py` | Rich terminal output, JSON file, SMTP email |
| `run.py` | Click CLI entry point (`htcondor_monitor.run:cli`) |
| `settings.py` | Pydantic `BaseSettings`; single module-level `settings` singleton imported everywhere |
