"""
Reporting — formats RunRecord findings into human-readable reports
and optionally sends them by email.
"""

from __future__ import annotations

import json
import logging
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from htcondor_monitor.config.settings import settings
from htcondor_monitor.state.store import RunRecord

logger = logging.getLogger(__name__)
console = Console()


# ── Terminal / rich output ─────────────────────────────────────────────────────

def print_report(record: RunRecord) -> None:
    """Print a styled report to stdout using Rich."""
    fj = record.findings_json
    ts = record.run_at.strftime("%Y-%m-%d %H:%M UTC")

    console.print()
    console.print(
        Panel(
            f"[bold cyan]{record.task_name.replace('_', ' ').title()}[/bold cyan]\n"
            f"[dim]{ts}  |  cadence: {record.cadence}  |  steps: {record.agent_steps}[/dim]",
            box=box.DOUBLE_EDGE,
        )
    )

    # Executive summary
    summary = fj.get("executive_summary", "")
    if summary:
        console.print(Panel(summary, title="Executive Summary", border_style="green"))

    # New / ongoing / resolved issues
    for section, colour in [
        ("new_issues", "red"),
        ("ongoing_issues", "yellow"),
        ("resolved_issues", "green"),
    ]:
        items = fj.get(section, [])
        if items:
            console.print(f"\n[bold {colour}]{section.replace('_', ' ').title()}[/bold {colour}]")
            for item in items:
                console.print(f"  • {item}")

    # Flagged users / nodes
    for key in ("flagged_users", "flagged_nodes"):
        vals = fj.get(key, [])
        if vals:
            console.print(f"\n[bold magenta]{key.replace('_', ' ').title()}:[/bold magenta] {', '.join(vals)}")

    # Recommendations (resource efficiency task)
    recs = fj.get("recommendations", {})
    if recs:
        table = Table(title="Recommendations", box=box.SIMPLE)
        table.add_column("User", style="cyan")
        table.add_column("Suggested RequestMemory (MB)")
        table.add_column("Suggested RequestCpus")
        for user, vals in recs.items():
            table.add_row(user, str(vals.get("RequestMemory", "—")), str(vals.get("RequestCpus", "—")))
        console.print(table)

    console.print()


# ── JSON file output ───────────────────────────────────────────────────────────

def save_json_report(record: RunRecord) -> Path:
    """Write the full findings JSON to the report output directory."""
    out_dir = settings.report_output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = record.run_at.strftime("%Y%m%d_%H%M")
    filename = f"{record.cadence}__{record.task_name}__{ts}.json"
    path = out_dir / filename
    path.write_text(json.dumps(record.to_dict(), indent=2))
    logger.info("JSON report saved to %s", path)
    return path


# ── Email output ───────────────────────────────────────────────────────────────

def _build_html(record: RunRecord) -> str:
    fj = record.findings_json
    ts = record.run_at.strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "<html><body>",
        f"<h2>{record.task_name.replace('_', ' ').title()}</h2>",
        f"<p><small>{ts} | cadence: {record.cadence}</small></p>",
        f"<h3>Executive Summary</h3><p>{fj.get('executive_summary', '')}</p>",
    ]

    for section, colour in [
        ("new_issues", "#c0392b"),
        ("ongoing_issues", "#e67e22"),
        ("resolved_issues", "#27ae60"),
    ]:
        items = fj.get(section, [])
        if items:
            lines.append(f'<h3 style="color:{colour}">{section.replace("_"," ").title()}</h3><ul>')
            for item in items:
                lines.append(f"<li>{item}</li>")
            lines.append("</ul>")

    for key in ("flagged_users", "flagged_nodes"):
        vals = fj.get(key, [])
        if vals:
            lines.append(f"<p><b>{key.replace('_',' ').title()}:</b> {', '.join(vals)}</p>")

    recs = fj.get("recommendations", {})
    if recs:
        lines.append("<h3>Recommendations</h3><table border='1' cellpadding='4'>")
        lines.append("<tr><th>User</th><th>RequestMemory (MB)</th><th>RequestCpus</th></tr>")
        for user, vals in recs.items():
            lines.append(
                f"<tr><td>{user}</td><td>{vals.get('RequestMemory','—')}</td>"
                f"<td>{vals.get('RequestCpus','—')}</td></tr>"
            )
        lines.append("</table>")

    lines.append("</body></html>")
    return "\n".join(lines)


def send_email_report(record: RunRecord) -> bool:
    """
    Send the findings by email.  Returns True if sent, False if skipped/failed.
    Skipped if HTCONDOR_REPORT_EMAIL_TO is empty.
    """
    if not settings.report_email_to:
        return False

    recipients = [r.strip() for r in settings.report_email_to.split(",") if r.strip()]
    if not recipients:
        return False

    ts = record.run_at.strftime("%Y-%m-%d")
    subject = (
        f"[HTCondor Monitor] {record.task_name.replace('_', ' ').title()} — "
        f"{record.cadence} — {ts}"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.report_email_from
    msg["To"] = ", ".join(recipients)

    plain = record.findings_json.get("executive_summary", "See attached JSON for details.")
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(_build_html(record), "html"))

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
            smtp.sendmail(settings.report_email_from, recipients, msg.as_string())
        logger.info("Email report sent to %s", recipients)
        return True
    except Exception as exc:
        logger.error("Failed to send email report: %s", exc)
        return False
