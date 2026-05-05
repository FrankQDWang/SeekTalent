from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from pathlib import Path

from seektalent.config import AppSettings
from seektalent.runtime.exact_llm_cache import clear_exact_llm_cache

PROD_RUN_RETENTION_DAYS = 7


def cleanup_runtime_artifacts(settings: AppSettings, *, now: datetime | None = None) -> None:
    clear_exact_llm_cache(settings)
    if settings.runtime_mode != "prod":
        return
    current_time = now or datetime.now()
    cleanup_old_artifact_collection(settings.artifacts_path / "runs", now=current_time, retention_days=PROD_RUN_RETENTION_DAYS)
    cleanup_old_artifact_collection(
        settings.artifacts_path / "benchmark-executions",
        now=current_time,
        retention_days=PROD_RUN_RETENTION_DAYS,
    )


def cleanup_old_artifact_collection(collection_root: Path, *, now: datetime, retention_days: int) -> None:
    if not collection_root.exists():
        return
    cutoff = (now - timedelta(days=retention_days)).date()
    for year_dir in collection_root.iterdir():
        if not year_dir.is_dir():
            continue
        for month_dir in year_dir.iterdir():
            if not month_dir.is_dir():
                continue
            for day_dir in month_dir.iterdir():
                if not day_dir.is_dir():
                    continue
                partition_date_text = f"{year_dir.name}-{month_dir.name}-{day_dir.name}"
                try:
                    partition_date = datetime.strptime(partition_date_text, "%Y-%m-%d").date()
                except ValueError:
                    partition_date = None
                if partition_date is None:
                    continue
                if partition_date < cutoff:
                    shutil.rmtree(day_dir, ignore_errors=True)
            if not any(month_dir.iterdir()):
                month_dir.rmdir()
        if not any(year_dir.iterdir()):
            year_dir.rmdir()
