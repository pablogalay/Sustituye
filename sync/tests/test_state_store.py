from educamadrid_sync.state_store import (
    load_imported_ids,
    load_pending,
    mark_imported,
    merge_new_rows,
    save_imported_ids,
    save_pending,
)


def test_merge_new_rows_adds_new_and_skips_already_imported():
    rows = [{"id": "1", "x": "a"}, {"id": "2", "x": "b"}, {"id": "3", "x": "c"}]
    pending = merge_new_rows(rows, "id", pending={}, imported_ids={"2"})
    assert set(pending) == {"1", "3"}
    assert pending["1"] == {"id": "1", "x": "a"}


def test_merge_new_rows_is_idempotent_for_rows_already_pending():
    rows = [{"id": "1", "x": "a"}]
    first = merge_new_rows(rows, "id", pending={}, imported_ids=set())
    second = merge_new_rows(rows, "id", pending=first, imported_ids=set())
    assert second == {"1": {"id": "1", "x": "a"}}


def test_merge_new_rows_skips_rows_without_id():
    rows = [{"id": "", "x": "a"}, {"x": "b"}]
    pending = merge_new_rows(rows, "id", pending={}, imported_ids=set())
    assert pending == {}


def test_mark_imported_removes_from_pending_and_adds_to_ledger():
    pending = {"1": {"x": "a"}, "2": {"x": "b"}}
    imported_ids = {"9"}
    new_pending, new_imported_ids = mark_imported("1", pending, imported_ids)
    assert new_pending == {"2": {"x": "b"}}
    assert new_imported_ids == {"9", "1"}
    # inputs are not mutated
    assert pending == {"1": {"x": "a"}, "2": {"x": "b"}}
    assert imported_ids == {"9"}


def test_pending_roundtrip(tmp_path):
    path = tmp_path / "pending_responses.json"
    save_pending(path, {"1": {"x": "a"}})
    assert load_pending(path) == {"1": {"x": "a"}}


def test_pending_load_missing_file_returns_empty(tmp_path):
    assert load_pending(tmp_path / "missing.json") == {}


def test_imported_ids_roundtrip(tmp_path):
    path = tmp_path / "imported_ids.json"
    save_imported_ids(path, {"3", "1", "2"})
    assert load_imported_ids(path) == {"1", "2", "3"}


def test_imported_ids_load_missing_file_returns_empty_set(tmp_path):
    assert load_imported_ids(tmp_path / "missing.json") == set()
