#!/usr/bin/env python3
"""Emit a bounded, read-only snapshot for the daily cron summary job."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3


def _timestamp(value: object) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _load_jobs(path: Path) -> list[dict]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    jobs = value if isinstance(value, list) else value.get("jobs", []) if isinstance(value, dict) else []
    return [job for job in jobs if isinstance(job, dict) and job.get("id")]


def _executions(path: Path, cutoff: datetime) -> dict[str, list[dict]]:
    if not path.exists():
        return {}
    try:
        with sqlite3.connect(path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT id, job_id, source, status, claimed_at, started_at, finished_at, error "
                "FROM executions WHERE claimed_at >= ? ORDER BY claimed_at ASC",
                (cutoff.isoformat(),),
            ).fetchall()
    except (OSError, sqlite3.Error):
        return {}
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        record = dict(row)
        grouped.setdefault(str(record["job_id"]), []).append(record)
    return grouped


def _output_excerpt(output_dir: Path, job_id: str) -> str | None:
    files = sorted((output_dir / job_id).glob("*.md")) if (output_dir / job_id).exists() else []
    if not files:
        return None
    try:
        text = files[-1].read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if "## Response" in text:
        text = text.split("## Response", 1)[1].strip()
    elif "\n---\n" in text:
        text = text.split("\n---\n", 1)[1].strip()
    if len(text) > 1200:
        return text[:1200].rstrip() + "…"
    return text or None


def collect(home: Path, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    cron_dir = home / "cron"
    jobs = _load_jobs(cron_dir / "jobs.json")
    attempts = _executions(cron_dir / "executions.db", cutoff)
    by_id = {str(job["id"]): job for job in jobs}
    selected_ids = set(attempts)
    selected_ids.update(str(job["id"]) for job in jobs if job.get("enabled"))

    items = []
    for job_id in sorted(selected_ids):
        job = by_id.get(job_id)
        if not job:
            items.append({"job_id": job_id, "record_status": "记录不足", "executions": attempts[job_id]})
            continue
        item = {
            "job_id": job_id,
            "name": job.get("name") or job_id,
            "enabled": bool(job.get("enabled")),
            "schedule": job.get("schedule_display") or job.get("schedule"),
            "last_run_at": job.get("last_run_at"),
            "last_status": job.get("last_status"),
            "next_run_at": job.get("next_run_at"),
            "executions": attempts.get(job_id, []),
            "record_status": "完整" if attempts.get(job_id) else "过去24小时无执行记录",
        }
        # The summary job's previous report can contain stale caveats about
        # access that this snapshot is specifically designed to replace.
        # Keep its execution ledger, but do not feed that self-referential
        # prose back into the next report.
        if job.get("script") != "cron_daily_summary.py":
            excerpt = _output_excerpt(cron_dir / "output", job_id)
            if excerpt:
                item["latest_output_excerpt"] = excerpt
        items.append(item)

    return {
        "source": "Hermes cron scheduler read-only snapshot",
        "window_start_utc": cutoff.isoformat(),
        "window_end_utc": now.isoformat(),
        "jobs_file": str(cron_dir / "jobs.json"),
        "execution_count": sum(len(records) for records in attempts.values()),
        "jobs": items,
    }


def main() -> int:
    home = Path(os.environ.get("HERMES_HOME", "/opt/data")).expanduser()
    print(json.dumps(collect(home), ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
