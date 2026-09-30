import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
import importlib.util
from pathlib import Path

try:
    from team.scripts.cron_daily_summary import collect
except ModuleNotFoundError:
    spec = importlib.util.spec_from_file_location(
        "cron_daily_summary", "/opt/data/scripts/cron_daily_summary.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    collect = module.collect


class CronDailySummaryTests(unittest.TestCase):
    def test_collect_includes_enabled_jobs_and_recent_attempts(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            cron = home / "cron"
            (cron / "output" / "job-a").mkdir(parents=True)
            (cron / "jobs.json").write_text(json.dumps([
                {"id": "job-a", "name": "有记录", "enabled": True, "schedule_display": "every 60m", "last_run_at": "2026-09-23T00:00:00+00:00", "last_status": "ok", "next_run_at": "2026-09-23T01:00:00+00:00"},
                {"id": "job-b", "name": "无记录", "enabled": True, "schedule_display": "22 23 * * *"},
                {"id": "job-c", "name": "已停用", "enabled": False, "schedule_display": "once"},
            ]), encoding="utf-8")
            with sqlite3.connect(cron / "executions.db") as connection:
                connection.execute("CREATE TABLE executions (id TEXT, job_id TEXT, source TEXT, status TEXT, claimed_at TEXT, started_at TEXT, finished_at TEXT, error TEXT)")
                connection.execute("INSERT INTO executions VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ("run-1", "job-a", "builtin", "completed", "2026-09-22T13:00:00+00:00", "2026-09-22T13:00:01+00:00", "2026-09-22T13:00:02+00:00", None))
                connection.execute("INSERT INTO executions VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ("old", "job-c", "builtin", "failed", "2026-09-20T13:00:00+00:00", None, None, "old"))
            result = collect(home, datetime(2026, 9, 23, 13, 0, tzinfo=timezone.utc))
            self.assertEqual(result["execution_count"], 1)
            self.assertEqual({item["job_id"] for item in result["jobs"]}, {"job-a", "job-b"})
            self.assertEqual(result["jobs"][0]["executions"][0]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
