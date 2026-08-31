from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from playwright.sync_api import Error as PlaywrightError

from educamadrid_sync.browser_utils import (
    _block_unnecessary_resources,
    browser_page,
    retry_with_backoff,
)


@contextmanager
def _fake_sync_playwright(playwright):
    yield playwright


def _fake_browser_and_context():
    fake_page = MagicMock(name="page")
    fake_context = MagicMock(name="context")
    fake_context.new_page.return_value = fake_page
    fake_browser = MagicMock(name="browser")
    fake_browser.new_context.return_value = fake_context
    fake_playwright = MagicMock(name="playwright")
    fake_playwright.chromium.launch.return_value = fake_browser
    return fake_playwright, fake_browser, fake_context, fake_page


# --- resource cleanup -------------------------------------------------------


def test_browser_page_closes_context_and_browser_on_success():
    fake_playwright, fake_browser, fake_context, fake_page = _fake_browser_and_context()

    with patch(
        "educamadrid_sync.browser_utils.sync_playwright",
        return_value=_fake_sync_playwright(fake_playwright),
    ):
        with browser_page(headless=True) as page:
            assert page is fake_page

    fake_context.close.assert_called_once()
    fake_browser.close.assert_called_once()


def test_browser_page_closes_resources_when_body_raises():
    fake_playwright, fake_browser, fake_context, _ = _fake_browser_and_context()

    with patch(
        "educamadrid_sync.browser_utils.sync_playwright",
        return_value=_fake_sync_playwright(fake_playwright),
    ):
        with pytest.raises(RuntimeError, match="boom"):
            with browser_page(headless=True):
                raise RuntimeError("boom")

    fake_context.close.assert_called_once()
    fake_browser.close.assert_called_once()


def test_browser_page_closes_browser_even_if_context_creation_fails():
    """Regression test: launch() followed by a bare new_context() with no
    enclosing try leaks the browser process if new_context() raises."""
    fake_browser = MagicMock(name="browser")
    fake_browser.new_context.side_effect = RuntimeError("context boom")
    fake_playwright = MagicMock(name="playwright")
    fake_playwright.chromium.launch.return_value = fake_browser

    with patch(
        "educamadrid_sync.browser_utils.sync_playwright",
        return_value=_fake_sync_playwright(fake_playwright),
    ):
        with pytest.raises(RuntimeError, match="context boom"):
            with browser_page(headless=True):
                pass  # pragma: no cover - never reached

    fake_browser.close.assert_called_once()


def test_browser_page_propagates_launch_failure_without_touching_close():
    fake_playwright = MagicMock(name="playwright")
    fake_playwright.chromium.launch.side_effect = PlaywrightError("could not launch chromium")

    with patch(
        "educamadrid_sync.browser_utils.sync_playwright",
        return_value=_fake_sync_playwright(fake_playwright),
    ):
        with pytest.raises(PlaywrightError, match="could not launch chromium"):
            with browser_page(headless=True):
                pass  # pragma: no cover - never reached


def test_browser_page_swallows_browser_close_failure():
    fake_playwright, fake_browser, fake_context, _ = _fake_browser_and_context()
    fake_browser.close.side_effect = PlaywrightError("close failed")

    with patch(
        "educamadrid_sync.browser_utils.sync_playwright",
        return_value=_fake_sync_playwright(fake_playwright),
    ):
        with browser_page(headless=True):
            pass  # must not raise despite browser.close() failing

    fake_context.close.assert_called_once()
    fake_browser.close.assert_called_once()


def test_browser_page_still_closes_browser_if_context_close_itself_fails():
    fake_playwright, fake_browser, fake_context, _ = _fake_browser_and_context()
    fake_context.close.side_effect = PlaywrightError("close failed")

    with patch(
        "educamadrid_sync.browser_utils.sync_playwright",
        return_value=_fake_sync_playwright(fake_playwright),
    ):
        with browser_page(headless=True):
            pass

    fake_context.close.assert_called_once()
    fake_browser.close.assert_called_once()


# --- blocking unnecessary resources ------------------------------------------


def test_browser_page_registers_a_route_to_block_unnecessary_resources():
    fake_playwright, fake_browser, fake_context, _ = _fake_browser_and_context()

    with patch(
        "educamadrid_sync.browser_utils.sync_playwright",
        return_value=_fake_sync_playwright(fake_playwright),
    ):
        with browser_page(headless=True):
            pass

    fake_context.route.assert_called_once_with("**/*", _block_unnecessary_resources)


@pytest.mark.parametrize("resource_type", ["image", "font", "media"])
def test_blocks_image_font_and_media_requests(resource_type):
    route = MagicMock()
    route.request.resource_type = resource_type

    _block_unnecessary_resources(route)

    route.abort.assert_called_once()
    route.continue_.assert_not_called()


@pytest.mark.parametrize("resource_type", ["document", "script", "stylesheet", "xhr", "fetch"])
def test_allows_everything_else_through(resource_type):
    route = MagicMock()
    route.request.resource_type = resource_type

    _block_unnecessary_resources(route)

    route.continue_.assert_called_once()
    route.abort.assert_not_called()


# --- retries with exponential backoff ---------------------------------------


def test_retry_with_backoff_returns_result_on_first_success():
    operation = MagicMock(return_value="ok")

    with patch("educamadrid_sync.browser_utils.time.sleep") as sleep:
        result = retry_with_backoff(operation, attempts=3)

    assert result == "ok"
    operation.assert_called_once()
    sleep.assert_not_called()


def test_retry_with_backoff_retries_on_connection_failure_then_succeeds():
    operation = MagicMock(
        side_effect=[
            PlaywrightError("net::ERR_CONNECTION_RESET"),
            PlaywrightError("net::ERR_CONNECTION_RESET"),
            "ok",
        ]
    )

    with patch("educamadrid_sync.browser_utils.time.sleep") as sleep:
        result = retry_with_backoff(operation, attempts=3, base_delay_s=1.0, max_delay_s=8.0)

    assert result == "ok"
    assert operation.call_count == 3
    assert sleep.call_count == 2
    first_delay, second_delay = (call.args[0] for call in sleep.call_args_list)
    assert first_delay < second_delay  # exponential growth, not a fixed interval


def test_retry_with_backoff_raises_last_error_after_exhausting_attempts():
    operation = MagicMock(side_effect=PlaywrightError("still down"))

    with patch("educamadrid_sync.browser_utils.time.sleep"):
        with pytest.raises(PlaywrightError, match="still down"):
            retry_with_backoff(operation, attempts=3)

    assert operation.call_count == 3


def test_retry_with_backoff_does_not_retry_non_connection_errors():
    operation = MagicMock(side_effect=ValueError("not a connection problem"))

    with patch("educamadrid_sync.browser_utils.time.sleep") as sleep:
        with pytest.raises(ValueError):
            retry_with_backoff(operation, attempts=3)

    operation.assert_called_once()
    sleep.assert_not_called()


def test_retry_with_backoff_with_zero_attempts_never_calls_operation():
    """Edge case for a misconfigured attempts=0: the for-loop body never runs,
    so operation() is never called and the function silently returns None
    instead of the operation's result or a raised error - worth pinning down
    explicitly since it's an easy way to misconfigure a caller."""
    operation = MagicMock(return_value="ok")

    result = retry_with_backoff(operation, attempts=0)

    assert result is None
    operation.assert_not_called()


def test_retry_with_backoff_caps_delay_at_max_delay_s():
    operation = MagicMock(side_effect=[PlaywrightError("e")] * 4 + ["ok"])

    with patch("educamadrid_sync.browser_utils.time.sleep") as sleep:
        result = retry_with_backoff(operation, attempts=5, base_delay_s=10.0, max_delay_s=12.0)

    assert result == "ok"
    for call in sleep.call_args_list:
        assert call.args[0] <= 12.0 * 1.1  # cap plus the largest possible jitter
