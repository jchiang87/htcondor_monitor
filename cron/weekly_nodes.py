#!/usr/bin/env python3
"""Cron entry point: weekly execute node health."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from htcondor_monitor import (MonitoringAgent, print_report,
                              save_json_report, send_email_report)

def main():
    agent = MonitoringAgent()
    record = agent.run(task_name="node_health", cadence="weekly")
    print_report(record)
    save_json_report(record)
    send_email_report(record)

if __name__ == "__main__":
    main()
