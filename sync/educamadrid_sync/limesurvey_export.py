import csv
import io
import logging
from urllib.parse import urlparse

from .browser_utils import browser_page, retry_with_backoff

logger = logging.getLogger(__name__)

# LimeSurvey's "Export results" screen normally has a single submit button
# labelled "Exportar" (Spanish UI). Adjust if the real screen differs - run
# once with headless=False to inspect the real page and its selectors.
EXPORT_BUTTON_TEXT = "Exportar"
EXPORT_TIMEOUT_MS = 30000


class ExportError(RuntimeError):
    pass


def _export_url(survey_url: str, survey_id: str) -> str:
    parsed = urlparse(survey_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    return f"{base}/index.php/admin/export/sa/exportresults/surveyid/{survey_id}"


def fetch_responses(
    storage_state: dict, survey_url: str, survey_id: str, headless: bool = True, *, attempts: int = 3
) -> list[dict]:
    """Reuse an already-authenticated session to download the survey's CSV export
    and parse it into a list of row dicts, one per response. Transient connection/
    navigation failures are retried with exponential backoff."""

    def _attempt() -> list[dict]:
        with browser_page(headless=headless, storage_state=storage_state, accept_downloads=True) as page:
            page.goto(_export_url(survey_url, survey_id), wait_until="networkidle", timeout=EXPORT_TIMEOUT_MS)
            with page.expect_download(timeout=EXPORT_TIMEOUT_MS) as download_info:
                page.get_by_role("button", name=EXPORT_BUTTON_TEXT).click()
            download = download_info.value
            downloaded_path = download.path()
            if downloaded_path is None:
                raise ExportError("LimeSurvey export did not produce a downloadable file")
            content = downloaded_path.read_bytes().decode("utf-8-sig")
            rows = _parse_csv(content)
            logger.info("Fetched %d response rows from the survey export", len(rows))
            return rows

    return retry_with_backoff(_attempt, attempts=attempts)


def _parse_csv(content: str) -> list[dict]:
    sample = content[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";"
    reader = csv.DictReader(io.StringIO(content), dialect=dialect)
    return [row for row in reader]
