import csv

from autoapply.core.models import JobRecord, Outcome
from autoapply.core.store import CsvJobStore


def rec(key, outcome=Outcome.SUBMITTED, reason="submission_confirmed", company="Acme"):
    return JobRecord(key, "Dev", company, "Pune", outcome, reason, "https://x")


def test_header_and_rows_written(tmp_path):
    store = CsvJobStore(tmp_path / "d" / "a.csv")  # parent dir is created
    store.record(rec("1", company="Kognito Kube"))
    rows = list(csv.DictReader((tmp_path / "d" / "a.csv").open(encoding="utf-8")))
    assert rows[0]["company"] == "Kognito Kube" and rows[0]["outcome"] == "submitted"


def test_seen_survives_restart(tmp_path):
    CsvJobStore(tmp_path / "a.csv").record(rec("1"))
    assert CsvJobStore(tmp_path / "a.csv").seen("1")
    assert not CsvJobStore(tmp_path / "a.csv").seen("2")


def test_latest_row_wins_and_counts_follow_it(tmp_path):
    store = CsvJobStore(tmp_path / "a.csv")
    store.record(rec("1", Outcome.NEEDS_MANUAL, "captcha"))
    store.record(rec("1", Outcome.SUBMITTED))
    store.record(rec("2", Outcome.SKIPPED, "requires_questions"))
    assert store.counts() == {"submitted": 1, "skipped": 1, "needs_manual": 0}
    assert CsvJobStore(tmp_path / "a.csv").counts() == store.counts()


def test_xlsx_export_created(tmp_path):
    store = CsvJobStore(tmp_path / "a.csv")
    store.record(rec("1"))
    assert store.export_path.exists()


def test_commas_and_quotes_in_fields_round_trip(tmp_path):
    store = CsvJobStore(tmp_path / "a.csv")
    store.record(rec("1", company='Acme, "Inc"'))
    assert CsvJobStore(tmp_path / "a.csv")._latest["1"]["company"] == 'Acme, "Inc"'
