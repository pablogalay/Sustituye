import logging
import random
import time
from contextlib import contextmanager

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Route, sync_playwright

logger = logging.getLogger(__name__)

# Neither login nor the LimeSurvey export screen need images/fonts/media to
# function - blocking them cuts navigation time noticeably on slow links.
BLOCKED_RESOURCE_TYPES = {"image", "font", "media"}

DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_BASE_DELAY_S = 1.0
DEFAULT_RETRY_MAX_DELAY_S = 8.0


def _block_unnecessary_resources(route: Route) -> None:
    if route.request.resource_type in BLOCKED_RESOURCE_TYPES:
        route.abort()
    else:
        route.continue_()


@contextmanager
def browser_page(*, headless: bool, storage_state: dict | None = None, accept_downloads: bool = False):
    """Launch a Chromium browser/context/page and guarantee both are closed on the
    way out, even if launch or context/page creation fails partway through (a bare
    launch() followed by new_context() with no enclosing try leaks the browser
    process if new_context() or new_page() raises). Also blocks image/font/media
    requests so navigation isn't spent downloading assets nothing here reads."""
    with sync_playwright() as playwright:
        browser = None
        context = None
        try:
            browser = playwright.chromium.launch(headless=headless)
            context = browser.new_context(storage_state=storage_state, accept_downloads=accept_downloads)
            context.route("**/*", _block_unnecessary_resources)
            page = context.new_page()
            yield page
        finally:
            if context is not None:
                try:
                    context.close()
                except PlaywrightError:
                    logger.warning("Failed to close Playwright context cleanly", exc_info=True)
            if browser is not None:
                try:
                    browser.close()
                except PlaywrightError:
                    logger.warning("Failed to close Playwright browser cleanly", exc_info=True)


def retry_with_backoff(
    operation,
    *,
    attempts: int = DEFAULT_RETRY_ATTEMPTS,
    base_delay_s: float = DEFAULT_RETRY_BASE_DELAY_S,
    max_delay_s: float = DEFAULT_RETRY_MAX_DELAY_S,
    retry_on: tuple[type[BaseException], ...] = (PlaywrightError,),
):
    """Call operation() up to `attempts` times, retrying only on `retry_on`
    exceptions (connection drops, navigation timeouts) with exponential backoff
    plus jitter between attempts. Anything else - e.g. LoginError for bad
    credentials - propagates on the first try since retrying it can't help."""
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except retry_on as exc:
            if attempt == attempts:
                raise
            delay = min(max_delay_s, base_delay_s * (2 ** (attempt - 1)))
            delay += random.uniform(0, delay * 0.1)
            logger.warning(
                "Attempt %d/%d failed (%s); retrying in %.1fs", attempt, attempts, exc, delay,
            )
            time.sleep(delay)
