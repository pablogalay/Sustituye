import logging

from .browser_utils import browser_page, retry_with_backoff

logger = logging.getLogger(__name__)

# Keycloak's default theme uses these ids consistently across deployments. If
# EducaMadrid customized the login theme, adjust the three selectors below
# (run once with headless=False to inspect the real page).
USERNAME_SELECTOR = "#username"
PASSWORD_SELECTOR = "#password"
SUBMIT_SELECTOR = "#kc-login"
LOGIN_TIMEOUT_MS = 20000


class LoginError(RuntimeError):
    pass


def login(survey_url: str, username: str, password: str, headless: bool = True, *, attempts: int = 3) -> dict:
    """Drive the EducaMadrid Keycloak SSO login and return a Playwright storage_state
    (cookies only) that callers can reuse for later requests. The password is only
    ever passed to Playwright's fill() call - it is never logged or included in the
    returned state.

    Transient connection/navigation failures (dropped connections, timeouts) are
    retried with exponential backoff. A LoginError - wrong credentials or a changed
    login form - is not retried since trying again can't fix it."""

    def _attempt() -> dict:
        with browser_page(headless=headless) as page:
            page.goto(survey_url, wait_until="networkidle", timeout=LOGIN_TIMEOUT_MS)
            if "login.educa.madrid.org" in page.url:
                logger.info("Redirected to Keycloak SSO login, submitting credentials")
                page.wait_for_selector(USERNAME_SELECTOR, timeout=LOGIN_TIMEOUT_MS)
                page.fill(USERNAME_SELECTOR, username)
                page.fill(PASSWORD_SELECTOR, password)
                page.click(SUBMIT_SELECTOR)
                page.wait_for_load_state("networkidle", timeout=LOGIN_TIMEOUT_MS)
            if "login.educa.madrid.org" in page.url or "formularios.educa.madrid.org" not in page.url:
                raise LoginError(
                    "EducaMadrid login did not complete - check EDUCAMADRID_USERNAME/"
                    f"EDUCAMADRID_PASSWORD or whether the Keycloak login form changed. "
                    f"Final URL: {page.url}"
                )
            logger.info("EducaMadrid login succeeded")
            return page.context.storage_state()

    return retry_with_backoff(_attempt, attempts=attempts)
