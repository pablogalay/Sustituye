from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from educamadrid_sync.limesurvey_export import ExportError, fetch_responses


def _fake_page_with_download(csv_path) -> MagicMock:
    page = MagicMock()
    download_info = MagicMock()
    download_info.value.path.return_value = csv_path
    page.expect_download.return_value.__enter__.return_value = download_info
    page.expect_download.return_value.__exit__.return_value = False
    return page


def test_fetch_responses_retries_on_connection_failure_then_succeeds(tmp_path):
    csv_path = tmp_path / "export.csv"
    csv_path.write_text("id;submitdate\n1;2026-03-05 10:00:00\n", encoding="utf-8")
    page = _fake_page_with_download(csv_path)
    calls = {"n": 0}

    @contextmanager
    def fake_browser_page(**kwargs):
        calls["n"] += 1
        if calls["n"] < 2:
            raise PlaywrightTimeoutError("net::ERR_CONNECTION_RESET")
        yield page

    with patch("educamadrid_sync.limesurvey_export.browser_page", fake_browser_page), patch(
        "educamadrid_sync.browser_utils.time.sleep"
    ):
        rows = fetch_responses(
            {"cookies": []}, "https://formularios.educa.madrid.org/survey", "42", attempts=3
        )

    assert rows == [{"id": "1", "submitdate": "2026-03-05 10:00:00"}]
    assert calls["n"] == 2


def test_parses_csv_when_sniffer_cannot_detect_a_delimiter(tmp_path):
    """A single-column export has no ';', ',' or tab for csv.Sniffer to find,
    so it raises csv.Error and _parse_csv must fall back to ';' instead of
    propagating the error and losing the whole export."""
    csv_path = tmp_path / "export.csv"
    csv_path.write_text("onlyonecolumn\nvalue1\nvalue2\n", encoding="utf-8")
    page = _fake_page_with_download(csv_path)

    @contextmanager
    def fake_browser_page(**kwargs):
        yield page

    with patch("educamadrid_sync.limesurvey_export.browser_page", fake_browser_page), patch(
        "educamadrid_sync.browser_utils.time.sleep"
    ):
        rows = fetch_responses({"cookies": []}, "https://formularios.educa.madrid.org/survey", "42", attempts=1)

    assert rows == [{"onlyonecolumn": "value1"}, {"onlyonecolumn": "value2"}]


def test_parses_empty_export_with_only_a_header_row(tmp_path):
    csv_path = tmp_path / "export.csv"
    csv_path.write_text("id;submitdate\n", encoding="utf-8")
    page = _fake_page_with_download(csv_path)

    @contextmanager
    def fake_browser_page(**kwargs):
        yield page

    with patch("educamadrid_sync.limesurvey_export.browser_page", fake_browser_page), patch(
        "educamadrid_sync.browser_utils.time.sleep"
    ):
        rows = fetch_responses({"cookies": []}, "https://formularios.educa.madrid.org/survey", "42", attempts=1)

    assert rows == []


def test_parses_a_large_export_of_thousands_of_rows(tmp_path):
    csv_path = tmp_path / "export.csv"
    lines = ["id;submitdate"] + [f"{i};2026-03-05 10:00:00" for i in range(1, 5001)]
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    page = _fake_page_with_download(csv_path)

    @contextmanager
    def fake_browser_page(**kwargs):
        yield page

    with patch("educamadrid_sync.limesurvey_export.browser_page", fake_browser_page), patch(
        "educamadrid_sync.browser_utils.time.sleep"
    ):
        rows = fetch_responses({"cookies": []}, "https://formularios.educa.madrid.org/survey", "42", attempts=1)

    assert len(rows) == 5000
    assert rows[0]["id"] == "1"
    assert rows[-1]["id"] == "5000"


def test_parses_export_with_ragged_rows_without_crashing(tmp_path):
    """A respondent's free-text answer containing the delimiter character (or a
    truncated export) can produce rows with more/fewer fields than the header;
    this must not crash the whole sync run."""
    csv_path = tmp_path / "export.csv"
    csv_path.write_text("id;submitdate;task_left\n1;2026-03-05 10:00:00;short\n2;2026-03-05 10:00:00;too;many;fields\n", encoding="utf-8")
    page = _fake_page_with_download(csv_path)

    @contextmanager
    def fake_browser_page(**kwargs):
        yield page

    with patch("educamadrid_sync.limesurvey_export.browser_page", fake_browser_page), patch(
        "educamadrid_sync.browser_utils.time.sleep"
    ):
        rows = fetch_responses({"cookies": []}, "https://formularios.educa.madrid.org/survey", "42", attempts=1)

    assert len(rows) == 2
    assert rows[0]["task_left"] == "short"


def test_fetch_responses_does_not_retry_when_export_produces_no_file():
    page = MagicMock()
    download_info = MagicMock()
    download_info.value.path.return_value = None
    page.expect_download.return_value.__enter__.return_value = download_info
    page.expect_download.return_value.__exit__.return_value = False
    calls = {"n": 0}

    @contextmanager
    def fake_browser_page(**kwargs):
        calls["n"] += 1
        yield page

    with patch("educamadrid_sync.limesurvey_export.browser_page", fake_browser_page), patch(
        "educamadrid_sync.browser_utils.time.sleep"
    ) as sleep:
        with pytest.raises(ExportError):
            fetch_responses(
                {"cookies": []}, "https://formularios.educa.madrid.org/survey", "42", attempts=3
            )

    assert calls["n"] == 1
    sleep.assert_not_called()
