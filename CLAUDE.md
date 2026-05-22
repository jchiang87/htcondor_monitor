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

There is no test suite yet.

## Configuration

Copy `examples/.env.example` to `.env` at the repo root. All variables use the `HTCONDOR_` prefix (except `ANTHROPIC_API_KEY`). The `Settings` class in `htcondor_monitor/settings.py` is a Pydantic `BaseSettings` that loads from `.env` automatically.

The two most critical variables to set:
- `ANTHROPIC_API_KEY` — passed to smolagents' `OpenAIModel` wrapper
- `OPENSEARCH_HOST` — where the HTCondor ClassAd index lives

ClassAd field names are fully configurable via `FIELD_*` env vars (e.g., `HTCONDOR_FIELD_USER`), since different HTCondor deployments use different attribute names.

## Running

After `scons`, the CLI entry point is `bin/htcondor-monitor`:

```bash
htcondor-monitor tasks                          # list available monitoring tasks
htcondor-monitor print-prompt TASK_NAME         # preview rendered prompt (no LLM call)
htcondor-monitor run TASK_NAME [--dry-run]      # run a task
htcondor-monitor history TASK_NAME              # show prior run records
```

The `--dry-run` flag skips the LLM call and just renders the prompt.

Cron scripts in `cron/` are thin wrappers — each instantiates `MonitoringAgent`, calls `.run()`, and calls the three reporting methods.

## Architecture

```
cron/{daily,weekly,monthly}_*.py
        │
        ▼
MonitoringAgent.run(task_name, cadence)           monitoring_agent.py
  ├─ PromptBuilder.build()                        builder.py
  │    ├─ Jinja2 template (TEMPLATES dict)
  │    ├─ Settings (thresholds + field names)
  │    └─ StateStore.prior_context_block()        store.py
  │
  ├─ CodeAgent.run(prompt)                        smolagents
  │    ├─ OpenAIModel("claude-sonnet-...")
  │    └─ Tools from ALL_TOOLS                    opensearch_tools.py
  │
  └─ RunRecord → StateStore.save()
        └─ print_report / save_json / email       report.py
```

**Key design decisions:**
- The `CodeAgent` is agentic — it decides which OpenSearch tools to call and how many times. The prompt tells it what questions to answer; the tools give it raw data.
- `PromptBuilder` injects prior run summaries so the LLM can distinguish new from ongoing issues without any special logic — it's all done via prompt context.
- `StateStore` writes JSON files to `monitor_state/{cadence}__{task}.json`, keeping only the last `STATE_HISTORY_DEPTH` records.

## Monitoring Tasks (7 total)

Defined as Jinja2 template strings in `builder.py::TEMPLATES`:

| Task | Default cadence |
|------|----------------|
| `health_check` | daily |
| `anomaly_detection` | daily |
| `long_running_jobs` | daily/weekly |
| `resource_efficiency` | weekly |
| `node_health` | weekly |
| `gpu_utilization` | weekly |
| `user_behavior_trends` | monthly |

**To add a new task**: add an entry to `TEMPLATES` in `builder.py` and optionally a cron script. The CLI picks it up automatically.

## OpenSearch Tools

Seven `@tool`-decorated functions in `opensearch_tools.py` are passed to the `CodeAgent`. The agent calls them autonomously:

- `query_jobs` — retrieve raw ClassAds with filters
- `aggregate_by_user` — per-user CPU/memory/disk statistics  
- `aggregate_by_node` — per-execute-node failure/eviction rates
- `get_hold_reason_summary` — frequency table of hold reasons
- `get_schema_sample` / `get_index_field_names` — schema discovery
- `run_raw_query` — arbitrary OpenSearch DSL passthrough

## Reports

- **Terminal**: Rich-formatted panels (red=new issues, yellow=ongoing, green=resolved)
- **JSON**: `reports/{cadence}__{task}_{timestamp}.json`
- **Email**: SMTP HTML+plaintext; configure `HTCONDOR_REPORT_EMAIL_TO` and `HTCONDOR_SMTP_HOST`
