import json
from datetime import date, datetime
from pathlib import Path

DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y")
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# LimeSurvey always includes a submission-date metadata column in response
# exports; it is only set once a respondent submits the survey, so an empty
# value means the response is still incomplete/abandoned. The header text
# depends on the export's language setting - this survey's export uses the
# Spanish label, but both are checked for robustness.
COMPLETION_COLUMNS = ("Fecha de envío", "submitdate")


class MappingError(RuntimeError):
    """A response row could not be turned into an absence payload. The row is
    left in pending_responses.json for manual review or a later retry, it is
    never guessed or dropped."""


def is_response_complete(row: dict) -> bool:
    for column in COMPLETION_COLUMNS:
        value = str(row.get(column, "") or "").strip()
        if value and value != "N/A":
            return True
    return False


def load_field_mapping(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_teacher_lookup(teachers: list[dict]) -> dict[str, int]:
    """Map the local part of each teacher's institutional email (before the
    '@') to their teacher id, matching how the survey identifies teachers."""
    lookup: dict[str, int] = {}
    for teacher in teachers:
        username = teacher["email"].split("@", 1)[0].strip().lower()
        lookup[username] = teacher["id"]
    return lookup


def build_timeslot_lookup(timeslots: list[dict]) -> dict[tuple[str, str], int]:
    """Key each timeslot by (weekday, normalized 'H:MM-H:MM' hour range) so it
    matches the survey's literal hour-range answers (e.g. '8:25-9:20')
    instead of the internal period_number, which the survey does not ask."""
    lookup: dict[tuple[str, str], int] = {}
    for slot in timeslots:
        time_range = _normalize_time_range(f"{slot['start_time']}-{slot['end_time']}")
        lookup[(slot["weekday"], time_range)] = slot["id"]
    return lookup


def _normalize_time_range(raw: str) -> str:
    parts = raw.strip().split("-")
    if len(parts) != 2:
        raise MappingError(f"Unrecognized hour range: {raw!r}")
    return f"{_normalize_time(parts[0])}-{_normalize_time(parts[1])}"


def _normalize_time(raw: str) -> str:
    pieces = raw.strip().split(":")
    if len(pieces) < 2:
        raise MappingError(f"Unrecognized time value: {raw!r}")
    try:
        hour, minute = int(pieces[0]), int(pieces[1])
    except ValueError:
        raise MappingError(f"Unrecognized time value: {raw!r}")
    return f"{hour}:{minute:02d}"


def _parse_date(raw: str) -> date:
    # LimeSurvey date questions export as "YYYY-MM-DD HH:MM:SS" (time is
    # always midnight for a date-only question); keep only the date part.
    raw = raw.strip().split(" ")[0]
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise MappingError(f"Unrecognized date format: {raw!r}")


def _column(row: dict, columns: dict, field: str) -> str:
    code = columns.get(field)
    if code is None:
        raise MappingError(f"field_mapping.json has no column configured for '{field}'")
    value = row.get(code, "")
    return value.strip() if isinstance(value, str) else value


def _optional_column(row: dict, columns: dict, field: str) -> str | None:
    code = columns.get(field)
    if code is None:
        return None
    value = row.get(code, "")
    value = value.strip() if isinstance(value, str) else value
    return value or None


def map_response_to_absence(
    row: dict,
    field_mapping: dict,
    teacher_lookup: dict[str, int],
    timeslot_lookup: dict[tuple[str, str], int],
) -> dict:
    """Turn one raw survey response row into an AbsenceIn-shaped payload
    (date, timeslot_id, absent_teacher_id, class_group, classroom, task_left,
    observations), or raise MappingError describing exactly what could not be
    resolved. Assumes the row already passed is_response_complete()."""
    columns = field_mapping["columns"]

    username = _column(row, columns, "username").lower()
    if not username:
        raise MappingError("Row has no username value")
    absent_teacher_id = teacher_lookup.get(username)
    if absent_teacher_id is None:
        raise MappingError(f"No teacher found with email starting with '{username}@'")

    absence_date = _parse_date(_column(row, columns, "date"))
    weekday = WEEKDAY_NAMES[absence_date.weekday()]
    if weekday not in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday"):
        raise MappingError(f"Date {absence_date.isoformat()} falls on a weekend, no timeslot exists")

    time_range = _normalize_time_range(_column(row, columns, "period"))
    timeslot_id = timeslot_lookup.get((weekday, time_range))
    if timeslot_id is None:
        raise MappingError(f"No timeslot found for weekday={weekday!r} hours={time_range!r}")

    class_group = _column(row, columns, "class_group")
    classroom = _column(row, columns, "classroom")
    task_left = _column(row, columns, "task_left")
    if not class_group or not classroom or not task_left:
        raise MappingError("class_group, classroom and task_left are all required")

    observations = _optional_column(row, columns, "observations")

    return {
        "date": absence_date.isoformat(),
        "timeslot_id": timeslot_id,
        "absent_teacher_id": absent_teacher_id,
        "class_group": class_group,
        "classroom": classroom,
        "task_left": task_left,
        "observations": observations,
    }
