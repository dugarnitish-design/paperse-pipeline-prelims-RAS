#!/usr/bin/env python3
"""
Daily dispatcher — ONE Railway cron runs this every day; it decides what to run
based on the date, so you only manage a single cron instead of several.

  python3 pipelines/scheduled_jobs.py

Add a Railway cron that runs this once daily (e.g. `30 2 * * *` = 02:30 UTC / 08:00 IST):
  - Sunday        -> weekly_coverage_report.py   (§5D / §12 weekly coverage + readiness)
  - 1st of month  -> analyse_pyq_trends.py        (§11 RPSC-2026 prediction report)
  - any other day -> nothing
"""
import datetime
import subprocess
import sys
import pathlib

# Absolute paths so the jobs run regardless of the cron's working directory.
ROOT = pathlib.Path(__file__).resolve().parent.parent

today = datetime.date.today()
print(f"Scheduled jobs running for {today}")

# Sunday = weekly coverage report
if today.weekday() == 6:
    print("Sunday — running weekly coverage report")
    subprocess.run([sys.executable, str(ROOT / "pipelines" / "weekly_coverage_report.py")])

# 1st of month = monthly trend analysis
if today.day == 1:
    print("1st of month — running trend analysis")
    subprocess.run([sys.executable, str(ROOT / "pipelines" / "analyse_pyq_trends.py")])

if today.weekday() != 6 and today.day != 1:
    print("No scheduled jobs today. Exiting.")
