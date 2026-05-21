# htcondor_monitor

Agentic HTCondor job monitoring using **smolagents CodeAgent** + **OpenSearch** + **Anthropic Claude**.

The agent queries your HTCondor ClassAd history stored in OpenSearch, reasons over it, and produces structured findings reports — new vs ongoing vs resolved — with memory across runs.

---

## Architecture

```
cron job
  └─► cron/daily_health.py          (thin wrapper)
        └─► MonitoringAgent.run()
              ├─ PromptBuilder        renders Jinja2 template with live settings + prior context
              ├─ StateStore           loads rolling window of prior run summaries
              ├─ CodeAgent (smolagents)
              │     ├─ query_jobs()            OpenSearch tools
              │     ├─ aggregate_by_user()
              │     ├─ aggregate_by_node()
              │     ├─ get_hold_reason_summary()
              │     ├─ get_schema_sample()
              │     ├─ get_index_field_names()
              │     └─ run_raw_query()
              └─ Reporting            Rich terminal + JSON file + email
```

---

## Installation

```bash
git clone <repo>
cd htcondor_monitor
python -m venv venv
source venv/bin/activate
pip install -e .
```

Copy and edit the environment file:

```bash
cp .env.example .env
$EDITOR .env
```

---

## CLI usage

```bash
# List available tasks
htcondor-monitor tasks

# Preview a rendered prompt (no LLM call)
htcondor-monitor print-prompt health_check --cadence daily

# Dry-run (full prompt printed, LLM skipped)
htcondor-monitor run health_check --dry-run

# Run a task, print to terminal, save JSON report
htcondor-monitor run health_check --cadence daily --save-json

# Run and send email
htcondor-monitor run resource_efficiency --cadence weekly --save-json --email

# Show prior run history for a task
htcondor-monitor history health_check --cadence daily --depth 3
```

---

## Available tasks

| Task | Recommended cadence | What it analyses |
|---|---|---|
| `health_check` | Daily | Held/removed jobs, low CPU efficiency, memory overages, bad nodes |
| `anomaly_detection` | Daily | Statistical outliers in wall time, memory, restarts, failure bursts |
| `long_running_jobs` | Daily | Stalled, memory-leaking, or poorly checkpointed long jobs |
| `resource_efficiency` | Weekly | Per-user CPU/memory/disk request accuracy and waste ranking |
| `node_health` | Weekly | Execute node failure rates, shadow exceptions, exit code analysis |
| `gpu_utilization` | Weekly | GPU slot waste, CPU-only jobs in GPU slots, node occupancy |
| `user_behavior_trends` | Weekly/Monthly | Anomalous changes vs each user's own historical baseline |

---

## Cron setup

```crontab
# Daily — 06:00 and 06:10
0  6  * * *   /opt/htcondor_monitor/venv/bin/python /opt/htcondor_monitor/cron/daily_health.py   >> /var/log/htcondor_monitor/daily.log 2>&1
10 6  * * *   /opt/htcondor_monitor/venv/bin/python /opt/htcondor_monitor/cron/daily_anomaly.py  >> /var/log/htcondor_monitor/daily.log 2>&1

# Weekly (Monday) — 07:00–08:00
0  7  * * 1   /opt/htcondor_monitor/venv/bin/python /opt/htcondor_monitor/cron/weekly_efficiency.py >> /var/log/htcondor_monitor/weekly.log 2>&1
30 7  * * 1   /opt/htcondor_monitor/venv/bin/python /opt/htcondor_monitor/cron/weekly_nodes.py      >> /var/log/htcondor_monitor/weekly.log 2>&1
0  8  * * 1   /opt/htcondor_monitor/venv/bin/python /opt/htcondor_monitor/cron/weekly_gpu.py        >> /var/log/htcondor_monitor/weekly.log 2>&1

# Monthly (1st) — 08:00
0  8  1 * *   /opt/htcondor_monitor/venv/bin/python /opt/htcondor_monitor/cron/monthly_trends.py    >> /var/log/htcondor_monitor/monthly.log 2>&1
```

---

## Configuration reference

All settings can be set as environment variables with the `HTCONDOR_` prefix,
or in a `.env` file.  See `.env.example` for the full list.

### Key settings

| Variable | Default | Description |
|---|---|---|
| `HTCONDOR_OPENSEARCH_HOST` | `https://localhost:9200` | OpenSearch cluster URL |
| `HTCONDOR_OPENSEARCH_INDEX_PREFIX` | `htcondor-jobs` | Index name prefix |
| `HTCONDOR_ANTHROPIC_MODEL` | `claude-sonnet-4-20250514` | Model to use |
| `HTCONDOR_STATE_DIR` | `./monitor_state` | Where run history JSON files live |
| `HTCONDOR_STATE_HISTORY_DEPTH` | `5` | Prior runs to retain and inject as context |
| `HTCONDOR_REPORT_OUTPUT_DIR` | `./reports` | Where JSON reports are saved |
| `HTCONDOR_REPORT_EMAIL_TO` | *(empty)* | Comma-separated recipients; empty disables email |
| `HTCONDOR_CPU_EFFICIENCY_WARN_PCT` | `30.0` | CPU efficiency threshold (%) |
| `HTCONDOR_MEMORY_OVERREQUEST_RATIO` | `2.0` | Memory over-request flag ratio |
| `HTCONDOR_NODE_FAILURE_RATE_PCT` | `15.0` | Node failure rate threshold (%) |

### Field name overrides

If your ingest pipeline renames ClassAd attributes, override the `HTCONDOR_FIELD_*` variables, e.g.:

```bash
HTCONDOR_FIELD_MEMORY_USAGE=rss_kb
HTCONDOR_FIELD_WALL_TIME=wall_clock_seconds
```

---

## Extending

### Add a custom task

1. Add a new Jinja2 template string to `TEMPLATES` in `prompts/builder.py`.
2. Optionally add a dedicated cron script in `cron/`.
3. No other changes needed — the CLI picks up all keys in `TEMPLATES` automatically.

### Add a custom OpenSearch tool

```python
from smolagents import tool
from htcondor_monitor.tools.opensearch_tools import ALL_TOOLS

@tool
def my_custom_query(user: str) -> dict:
    """Describe what this does — the agent reads this docstring."""
    ...

# Pass to agent
from htcondor_monitor.agents.monitoring_agent import MonitoringAgent
agent = MonitoringAgent(extra_tools=[my_custom_query])
agent.run("health_check")
```

### Pass extra context to a prompt

```python
agent.run(
    "health_check",
    extra_prompt_vars={"site_maintenance_window": "Saturday 02:00–04:00 UTC"},
)
```

---

## State and continuity

Each run's JSON findings are saved under `STATE_DIR/` as
`{cadence}__{task_name}.json`.  The last `STATE_HISTORY_DEPTH` runs are
loaded and injected verbatim into the next run's prompt under the
**Prior Run Summaries** section.

The agent is instructed to label each finding as **NEW**, **ONGOING**, or
**RESOLVED** relative to that history, so reports don't re-alarm on
already-known issues.
