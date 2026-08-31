"""Orchestration tests for run.run()/run.main(): the browser automation
(browser_login, limesurvey_export) and the app API (AppClient) are mocked out
at the module boundary, while mapping.py and state_store.py run for real
against tmp_path so the persist-before-notify ordering and per-row error
handling are exercised end-to-end, the way they actually run in production."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from educamadrid_sync import run as run_module
from educamadrid_sync.app_client import AppClientError
from educamadrid_sync.mapping import MappingError

FIELD_MAPPING = {
    "response_id_column": "id",
    "columns": {
        "username": "Q01", "date": "Q02", "period": "Q03",
        "class_group": "Q04", "classroom": "Q05", "task_left": "Q06", "observations": "Q07",
    },
}
TEACHERS = [{"id": 1, "email": "ana.garcia@educa.madrid.org"}]
TIMESLOTS = [{"id": 10, "weekday": "Thursday", "period_number": 3, "start_time": "11:10:00", "end_time": "12:05:00"}]


def _make_settings(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    field_mapping_path = tmp_path / "field_mapping.json"
    field_mapping_path.write_text(json.dumps(FIELD_MAPPING), encoding="utf-8")
    settings = SimpleNamespace(
        survey_url="https://formularios.educa.madrid.org/survey",
        educamadrid_username="user", educamadrid_password="pass", survey_id="42",
        app_api_url="https://app.example.test", admin_email="admin@school.local", admin_password="admin123",
        headless=True, data_dir=data_dir,
        pending_responses_path=data_dir / "pending_responses.json",
        imported_ids_path=data_dir / "imported_ids.json",
        log_path=data_dir / "sync.log",
        field_mapping_path=field_mapping_path,
    )
    settings.ensure_data_dir = lambda: None
    return settings


def _row(row_id="1", **overrides):
    row = {
        "id": row_id, "submitdate": "2026-03-05 10:00:00",
        "Q01": "ana.garcia", "Q02": "2026-03-05", "Q03": "11:10-12:05",
        "Q04": "2ESO A", "Q05": "101", "Q06": "Ejercicios del libro", "Q07": "",
    }
    row.update(overrides)
    return row


def _fake_app_client(create_absence_side_effect=None):
    client = MagicMock(name="AppClient")
    client.get_teachers.return_value = TEACHERS
    client.get_timeslots.return_value = TIMESLOTS
    if create_absence_side_effect is not None:
        client.create_absence.side_effect = create_absence_side_effect
    else:
        client.create_absence.return_value = {"id": 1}
    return client


def _run_with(tmp_path, rows, app_client, headless=True):
    settings = _make_settings(tmp_path)
    with patch("educamadrid_sync.run.get_settings", return_value=settings), \
         patch("educamadrid_sync.run.browser_login.login", return_value={"cookies": []}), \
         patch("educamadrid_sync.run.limesurvey_export.fetch_responses", return_value=rows), \
         patch("educamadrid_sync.run.AppClient", return_value=app_client):
        return run_module.run(), settings


# --- happy path ----------------------------------------------------------------


def test_run_happy_path_imports_complete_rows_and_persists_state(tmp_path):
    client = _fake_app_client()
    result, settings = _run_with(tmp_path, [_row()], client)

    assert result == {"fetched": 1, "complete": 1, "imported": 1, "pending": 0}
    client.login.assert_called_once()
    client.create_absence.assert_called_once()
    client.close.assert_called_once()

    assert json.loads(settings.pending_responses_path.read_text(encoding="utf-8")) == {}
    assert json.loads(settings.imported_ids_path.read_text(encoding="utf-8")) == {"imported_ids": ["1"]}


def test_run_skips_rows_without_a_submission_date(tmp_path):
    client = _fake_app_client()
    rows = [_row(row_id="1", submitdate="2026-03-05 10:00:00"), _row(row_id="2", submitdate="")]
    result, _ = _run_with(tmp_path, rows, client)

    assert result["fetched"] == 2
    assert result["complete"] == 1
    assert result["imported"] == 1
    client.create_absence.assert_called_once()


def test_run_with_zero_fetched_rows_does_not_crash(tmp_path):
    client = _fake_app_client()
    result, _ = _run_with(tmp_path, [], client)

    assert result == {"fetched": 0, "complete": 0, "imported": 0, "pending": 0}
    client.create_absence.assert_not_called()
    client.login.assert_called_once()  # login still happens even with nothing to import
    client.close.assert_called_once()


# --- per-row error handling ------------------------------------------------------


def test_run_leaves_row_pending_on_mapping_error(tmp_path):
    client = _fake_app_client()
    # Q01 references a teacher not present in TEACHERS -> MappingError inside map_response_to_absence.
    result, settings = _run_with(tmp_path, [_row(Q01="nobody")], client)

    assert result == {"fetched": 1, "complete": 1, "imported": 0, "pending": 1}
    client.create_absence.assert_not_called()
    pending = json.loads(settings.pending_responses_path.read_text(encoding="utf-8"))
    assert "1" in pending


def test_run_leaves_row_pending_on_app_client_error(tmp_path):
    client = _fake_app_client(create_absence_side_effect=AppClientError("409 duplicate"))
    result, settings = _run_with(tmp_path, [_row()], client)

    assert result == {"fetched": 1, "complete": 1, "imported": 0, "pending": 1}
    pending = json.loads(settings.pending_responses_path.read_text(encoding="utf-8"))
    assert "1" in pending


def test_run_continues_processing_remaining_rows_after_one_fails(tmp_path):
    def side_effect(payload):
        if payload["class_group"] == "FAILS":
            raise AppClientError("500 server error")
        return {"id": 1}

    client = _fake_app_client(create_absence_side_effect=side_effect)
    rows = [_row(row_id="1", Q04="FAILS"), _row(row_id="2", Q04="OK")]
    result, _ = _run_with(tmp_path, rows, client)

    assert result == {"fetched": 2, "complete": 2, "imported": 1, "pending": 1}
    assert client.create_absence.call_count == 2


def test_run_pending_row_is_retried_on_a_later_call(tmp_path):
    """A row that failed to import must remain in pending_responses.json so the
    next scheduled run retries it, instead of silently being dropped."""
    settings = _make_settings(tmp_path)
    failing_client = _fake_app_client(create_absence_side_effect=AppClientError("503"))
    with patch("educamadrid_sync.run.get_settings", return_value=settings), \
         patch("educamadrid_sync.run.browser_login.login", return_value={"cookies": []}), \
         patch("educamadrid_sync.run.limesurvey_export.fetch_responses", return_value=[_row()]), \
         patch("educamadrid_sync.run.AppClient", return_value=failing_client):
        first_result = run_module.run()
    assert first_result["pending"] == 1

    succeeding_client = _fake_app_client()
    with patch("educamadrid_sync.run.get_settings", return_value=settings), \
         patch("educamadrid_sync.run.browser_login.login", return_value={"cookies": []}), \
         patch("educamadrid_sync.run.limesurvey_export.fetch_responses", return_value=[]), \
         patch("educamadrid_sync.run.AppClient", return_value=succeeding_client):
        second_result = run_module.run()
    # No new rows fetched this time, but the previously-pending one still gets retried.
    assert second_result == {"fetched": 0, "complete": 0, "imported": 1, "pending": 0}
    succeeding_client.create_absence.assert_called_once()


# --- persist-before-notify ordering -----------------------------------------------


def test_run_persists_pending_file_before_touching_the_app_api(tmp_path):
    settings = _make_settings(tmp_path)

    class OrderCheckingAppClient:
        def __init__(self, base_url, admin_email, admin_password):
            on_disk = json.loads(settings.pending_responses_path.read_text(encoding="utf-8"))
            assert "1" in on_disk, "pending_responses.json must be written before the app API client is constructed"

        def login(self):
            pass

        def get_teachers(self):
            return TEACHERS

        def get_timeslots(self):
            return TIMESLOTS

        def create_absence(self, payload):
            return {"id": 1}

        def close(self):
            pass

    with patch("educamadrid_sync.run.get_settings", return_value=settings), \
         patch("educamadrid_sync.run.browser_login.login", return_value={"cookies": []}), \
         patch("educamadrid_sync.run.limesurvey_export.fetch_responses", return_value=[_row()]), \
         patch("educamadrid_sync.run.AppClient", OrderCheckingAppClient):
        result = run_module.run()

    assert result["imported"] == 1


# --- volume: thousands of rows ----------------------------------------------------


def test_run_handles_thousands_of_rows(tmp_path):
    # NOTE: run() persists pending/imported state to disk on every single row
    # (see state_store.save_pending/save_imported_ids calls inside the loop in
    # run.py), so this is effectively O(n^2) I/O. 1000 rows already takes several
    # seconds; a truly production-sized batch (tens of thousands) would be a lot
    # slower than sync's ~90s HTTP timeout budget suggests it should be - see the
    # QA summary for details. Kept at 1000 to prove correctness at volume without
    # making the suite slow.
    ROW_COUNT = 1000
    client = _fake_app_client()
    rows = [_row(row_id=str(i), Q05=f"Room{i}") for i in range(1, ROW_COUNT + 1)]
    result, settings = _run_with(tmp_path, rows, client)

    assert result == {"fetched": ROW_COUNT, "complete": ROW_COUNT, "imported": ROW_COUNT, "pending": 0}
    assert client.create_absence.call_count == ROW_COUNT
    assert json.loads(settings.pending_responses_path.read_text(encoding="utf-8")) == {}
    imported_ids = json.loads(settings.imported_ids_path.read_text(encoding="utf-8"))["imported_ids"]
    assert len(imported_ids) == ROW_COUNT


# --- main() exit codes -------------------------------------------------------------


def test_main_exits_0_when_nothing_is_left_pending():
    with patch("educamadrid_sync.run.run", return_value={"fetched": 1, "complete": 1, "imported": 1, "pending": 0}):
        with pytest.raises(SystemExit) as exc_info:
            run_module.main()
    assert exc_info.value.code == 0


def test_main_exits_1_when_rows_are_left_pending():
    with patch("educamadrid_sync.run.run", return_value={"fetched": 1, "complete": 1, "imported": 0, "pending": 1}):
        with pytest.raises(SystemExit) as exc_info:
            run_module.main()
    assert exc_info.value.code == 1


def test_main_exits_2_when_run_raises_an_unhandled_exception():
    with patch("educamadrid_sync.run.run", side_effect=RuntimeError("browser crashed")):
        with pytest.raises(SystemExit) as exc_info:
            run_module.main()
    assert exc_info.value.code == 2
