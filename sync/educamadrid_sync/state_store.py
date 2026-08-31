import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def load_pending(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_pending(path: Path, pending: dict[str, dict]) -> None:
    path.write_text(json.dumps(pending, indent=2, ensure_ascii=False), encoding="utf-8")


def load_imported_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return set(data.get("imported_ids", []))


def save_imported_ids(path: Path, imported_ids: set[str]) -> None:
    payload = {"imported_ids": sorted(imported_ids)}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def merge_new_rows(
    rows: list[dict],
    response_id_column: str,
    pending: dict[str, dict],
    imported_ids: set[str],
) -> dict[str, dict]:
    """Add freshly fetched rows to the pending set, skipping anything already
    imported in a previous run. Returns a new dict; does not mutate `pending`."""
    merged = dict(pending)
    added = 0
    for row in rows:
        response_id = str(row.get(response_id_column, "")).strip()
        if not response_id:
            logger.warning("Skipping a response row with no %s column", response_id_column)
            continue
        if response_id in imported_ids:
            continue
        if response_id not in merged:
            added += 1
        merged[response_id] = row
    logger.info("Merged %d new response(s) into the pending set (%d total pending)", added, len(merged))
    return merged


def mark_imported(
    response_id: str,
    pending: dict[str, dict],
    imported_ids: set[str],
) -> tuple[dict[str, dict], set[str]]:
    """Remove a successfully-imported response from pending and add it to the
    imported ledger. Returns new (pending, imported_ids); does not mutate inputs."""
    new_pending = {k: v for k, v in pending.items() if k != response_id}
    new_imported_ids = set(imported_ids) | {response_id}
    return new_pending, new_imported_ids
