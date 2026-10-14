"""Persistence of processed jobs.

`JobStore` is the interface the engine depends on. `CsvJobStore` is the default
implementation (a plain CSV you can open in Excel). To move to a database,
implement the three methods of `JobStore` and pass it to the app; nothing else changes.

Semantics: the log is append-only; the *latest* row per job key is that job's
status. Any job that has a row at all counts as "seen" and is never retried
automatically (an interrupted job could already have been submitted). To retry
one, delete its rows from the file.
"""
from __future__ import annotations

import csv
import threading
from abc import ABC, abstractmethod
from dataclasses import asdict, fields
from pathlib import Path

from .models import JobRecord, Outcome


class JobStore(ABC):
    @abstractmethod
    def seen(self, job_key: str) -> bool:
        """True if this job has ever been recorded (any outcome)."""

    @abstractmethod
    def record(self, record: JobRecord) -> None:
        """Persist a record; a later record for the same key supersedes earlier ones."""

    @abstractmethod
    def counts(self) -> dict[str, int]:
        """All-time counts of latest outcomes, keyed by Outcome value."""


class CsvJobStore(JobStore):
    FIELDS = [f.name for f in fields(JobRecord)]

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.export_path = self.path.with_name(self.path.stem + "_latest.xlsx")
        self._lock = threading.Lock()
        self._latest: dict[str, dict[str, str]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        with self.path.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                if row.get("job_key"):
                    self._latest[row["job_key"]] = row

    def seen(self, job_key: str) -> bool:
        with self._lock:
            return job_key in self._latest

    def record(self, record: JobRecord) -> None:
        row = {k: ("" if v is None else str(getattr(v, "value", v))) for k, v in asdict(record).items()}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            is_new = not self.path.exists() or self.path.stat().st_size == 0
            # utf-8-sig so Excel on Windows opens accents and commas correctly.
            with self.path.open("a", newline="", encoding="utf-8-sig" if is_new else "utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=self.FIELDS)
                if is_new:
                    writer.writeheader()
                writer.writerow(row)
            self._latest[record.job_key] = row
            self._export_latest_xlsx()

    def _export_latest_xlsx(self) -> None:
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font
        except ImportError:
            return
        rows = sorted(self._latest.values(), key=lambda r: r.get("timestamp", ""), reverse=True)
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Applications"
        headers = self.FIELDS
        sheet.append(headers)
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        for row in rows:
            sheet.append([row.get(name, "") for name in headers])
        for column in sheet.columns:
            width = min(60, max((len(str(cell.value or "")) for cell in column), default=8) + 2)
            sheet.column_dimensions[column[0].column_letter].width = width
        workbook.save(self.export_path)

    def counts(self) -> dict[str, int]:
        with self._lock:
            totals = {outcome.value: 0 for outcome in Outcome}
            for row in self._latest.values():
                if row["outcome"] in totals:
                    totals[row["outcome"]] += 1
            return totals
