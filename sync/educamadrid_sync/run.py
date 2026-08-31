import logging
import sys

from . import browser_login, limesurvey_export, mapping, state_store
from .app_client import AppClient, AppClientError
from .settings import get_settings

logger = logging.getLogger(__name__)


def _configure_logging(log_path) -> None:
    handlers = [logging.StreamHandler(sys.stdout), logging.FileHandler(log_path, encoding="utf-8")]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )
    # Playwright/httpx are chatty at INFO; keep the sync's own logging readable.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def run() -> dict:
    settings = get_settings()
    settings.ensure_data_dir()
    _configure_logging(settings.log_path)

    logger.info("Starting EducaMadrid substitution sync")

    storage_state = browser_login.login(
        settings.survey_url, settings.educamadrid_username, settings.educamadrid_password, headless=settings.headless
    )
    rows = limesurvey_export.fetch_responses(
        storage_state, settings.survey_url, settings.survey_id, headless=settings.headless
    )
    complete_rows = [row for row in rows if mapping.is_response_complete(row)]
    logger.info(
        "%d of %d fetched responses are complete; %d still in progress and skipped",
        len(complete_rows), len(rows), len(rows) - len(complete_rows),
    )

    field_mapping = mapping.load_field_mapping(settings.field_mapping_path)
    response_id_column = field_mapping["response_id_column"]

    pending = state_store.load_pending(settings.pending_responses_path)
    imported_ids = state_store.load_imported_ids(settings.imported_ids_path)

    # Persist fetched responses to the local pending file *before* touching the
    # app API, so a later failure never loses data already pulled from EducaMadrid.
    pending = state_store.merge_new_rows(complete_rows, response_id_column, pending, imported_ids)
    state_store.save_pending(settings.pending_responses_path, pending)

    client = AppClient(settings.app_api_url, settings.admin_email, settings.admin_password)
    client.login()
    teacher_lookup = mapping.build_teacher_lookup(client.get_teachers())
    timeslot_lookup = mapping.build_timeslot_lookup(client.get_timeslots())

    imported_count = 0
    error_count = 0
    for response_id, row in list(pending.items()):
        try:
            payload = mapping.map_response_to_absence(row, field_mapping, teacher_lookup, timeslot_lookup)
            client.create_absence(payload)
        except (mapping.MappingError, AppClientError) as exc:
            logger.warning("Leaving response %s pending: %s", response_id, exc)
            error_count += 1
            continue

        pending, imported_ids = state_store.mark_imported(response_id, pending, imported_ids)
        state_store.save_pending(settings.pending_responses_path, pending)
        state_store.save_imported_ids(settings.imported_ids_path, imported_ids)
        imported_count += 1

    client.close()

    logger.info(
        "Sync finished: %d fetched (%d complete), %d imported, %d left pending",
        len(rows), len(complete_rows), imported_count, error_count,
    )
    return {
        "fetched": len(rows),
        "complete": len(complete_rows),
        "imported": imported_count,
        "pending": error_count,
    }


def main() -> None:
    try:
        result = run()
        sys.exit(1 if result["pending"] else 0)
    except Exception:
        logger.exception("EducaMadrid sync failed")
        sys.exit(2)


if __name__ == "__main__":
    main()
