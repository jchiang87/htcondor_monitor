"""
CLI entry point.  Designed to be invoked from cron.

Examples
--------
# Daily health check
htcondor-monitor run health_check --cadence daily

# Weekly resource efficiency review, save JSON report
htcondor-monitor run resource_efficiency --cadence weekly --save-json

# Dry-run to preview the prompt without calling the LLM
htcondor-monitor run anomaly_detection --dry-run

# List available tasks
htcondor-monitor tasks

# Show prior run state for a task
htcondor-monitor history health_check --cadence daily
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import click
from rich.console import Console

from .monitoring_agent import MonitoringAgent
from .settings import settings
from .builder import PromptBuilder
from .report import print_report, save_json_report, send_email_report
from .store import StateStore

console = Console()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


@click.group()
@click.option("--verbose", "-v", is_flag=True, default=False, help="Debug logging.")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """HTCondor agentic monitoring tool."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    _setup_logging(verbose)


@cli.command()
@click.argument("task_name")
@click.option(
    "--cadence",
    type=click.Choice(["daily", "weekly", "monthly", "adhoc"]),
    default=None,
    help="Override cadence (default: from settings).",
)
@click.option("--dry-run", is_flag=True, default=False, help="Print prompt; skip LLM call.")
@click.option("--save-json", is_flag=True, default=False, help="Write JSON report to disk.")
@click.option("--email", is_flag=True, default=False, help="Send email report after run.")
@click.option("--no-print", is_flag=True, default=False, help="Suppress terminal output.")
@click.pass_context
def run(
    ctx: click.Context,
    task_name: str,
    cadence: str | None,
    dry_run: bool,
    save_json: bool,
    email: bool,
    no_print: bool,
) -> None:
    """Run a single monitoring task by name."""
    builder = PromptBuilder()
    if task_name not in builder.available_tasks:
        console.print(f"[red]Unknown task '{task_name}'.[/red]  Available: {builder.available_tasks}")
        sys.exit(1)

    agent = MonitoringAgent()
    record = agent.run(task_name=task_name, cadence=cadence, dry_run=dry_run)

    if not dry_run:
        if not no_print:
            print_report(record)
        if save_json:
            path = save_json_report(record)
            console.print(f"[dim]JSON report: {path}[/dim]")
        if email:
            sent = send_email_report(record)
            if not sent:
                console.print("[yellow]Email not sent (check HTCONDOR_REPORT_EMAIL_TO setting).[/yellow]")


@cli.command()
def tasks() -> None:
    """List all available monitoring tasks."""
    builder = PromptBuilder()
    console.print("\n[bold]Available monitoring tasks:[/bold]\n")
    descriptions = {
        "health_check": "Daily — held/removed jobs, low CPU efficiency, memory overages, bad nodes",
        "resource_efficiency": "Weekly — per-user CPU/memory/disk request accuracy, waste ranking",
        "node_health": "Weekly — execute node failure rates, shadow exceptions, exit code analysis",
        "long_running_jobs": "Daily/Weekly — stalled, leaking, or inefficient long-running jobs",
        "user_behavior_trends": "Weekly/Monthly — anomalous changes in user submission patterns",
        "gpu_utilization": "Weekly — GPU slot waste, CPU-only jobs in GPU slots",
        "anomaly_detection": "Daily — statistical outliers in wall time, memory, restarts",
    }
    for name, desc in descriptions.items():
        console.print(f"  [cyan]{name:<28}[/cyan] {desc}")
    console.print()


@cli.command()
@click.argument("task_name")
@click.option(
    "--cadence",
    type=click.Choice(["daily", "weekly", "monthly", "adhoc"]),
    default=None,
)
@click.option("--depth", default=5, help="Number of prior runs to show.")
def history(task_name: str, cadence: str | None, depth: int) -> None:
    """Show the persisted run history for a task."""
    cadence = cadence or settings.cadence
    store = StateStore()
    records = store.load(cadence, task_name)
    if not records:
        console.print(f"[yellow]No history found for task='{task_name}' cadence='{cadence}'.[/yellow]")
        return
    for rec in records[-depth:]:
        ts = rec.run_at.strftime("%Y-%m-%d %H:%M UTC")
        console.print(f"\n[bold]{ts}[/bold]  steps={rec.agent_steps}")
        console.print(f"  {rec.findings_summary[:200]}")


@cli.command(name="print-prompt")
@click.argument("task_name")
@click.option("--cadence", default=None)
def print_prompt(task_name: str, cadence: str | None) -> None:
    """Print the rendered prompt for a task without running anything."""
    builder = PromptBuilder()
    try:
        prompt = builder.build(task_name=task_name, cadence=cadence)
        console.print(prompt)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)
