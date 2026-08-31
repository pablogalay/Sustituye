from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from educamadrid_sync.browser_login import LoginError, login


def _fake_page(final_url: str, storage_state=None) -> MagicMock:
    page = MagicMock()
    page.url = final_url
    page.context.storage_state.return_value = storage_state if storage_state is not None else {"cookies": []}
    return page


def test_login_retries_on_connection_failure_then_succeeds():
    page = _fake_page("https://formularios.educa.madrid.org/survey/done")
    calls = {"n": 0}

    @contextmanager
    def fake_browser_page(**kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PlaywrightTimeoutError("net::ERR_CONNECTION_RESET")
        yield page

    with patch("educamadrid_sync.browser_login.browser_page", fake_browser_page), patch(
        "educamadrid_sync.browser_utils.time.sleep"
    ):
        result = login(
            "https://formularios.educa.madrid.org/survey", "teacher", "s3cret", attempts=3
        )

    assert result == {"cookies": []}
    assert calls["n"] == 3


def test_login_gives_up_after_exhausting_attempts_on_persistent_connection_failure():
    calls = {"n": 0}

    @contextmanager
    def fake_browser_page(**kwargs):
        calls["n"] += 1
        raise PlaywrightError("net::ERR_CONNECTION_REFUSED")
        yield  # pragma: no cover - unreachable, keeps this a generator function

    with patch("educamadrid_sync.browser_login.browser_page", fake_browser_page), patch(
        "educamadrid_sync.browser_utils.time.sleep"
    ):
        with pytest.raises(PlaywrightError, match="ERR_CONNECTION_REFUSED"):
            login("https://formularios.educa.madrid.org/survey", "teacher", "s3cret", attempts=3)

    assert calls["n"] == 3


def test_login_does_not_retry_wrong_credentials():
    """A LoginError means the form was submitted and rejected - retrying with the
    same credentials can't help, so it must fail on the first attempt."""
    page = _fake_page("https://login.educa.madrid.org/still-here")
    calls = {"n": 0}

    @contextmanager
    def fake_browser_page(**kwargs):
        calls["n"] += 1
        yield page

    with patch("educamadrid_sync.browser_login.browser_page", fake_browser_page), patch(
        "educamadrid_sync.browser_utils.time.sleep"
    ) as sleep:
        with pytest.raises(LoginError):
            login("https://formularios.educa.madrid.org/survey", "teacher", "wrong", attempts=3)

    assert calls["n"] == 1
    sleep.assert_not_called()
