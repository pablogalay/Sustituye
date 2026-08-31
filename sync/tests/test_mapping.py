import json

import pytest

from educamadrid_sync.mapping import (
    MappingError,
    build_teacher_lookup,
    build_timeslot_lookup,
    is_response_complete,
    load_field_mapping,
    map_response_to_absence,
)

FIELD_MAPPING = {
    "response_id_column": "id",
    "columns": {
        "username": "Q01",
        "date": "Q02",
        "period": "Q03",
        "class_group": "Q04",
        "classroom": "Q05",
        "task_left": "Q06",
        "observations": "Q07",
    },
}

FIELD_MAPPING_NO_OBSERVATIONS = {
    "response_id_column": "id",
    "columns": {k: v for k, v in FIELD_MAPPING["columns"].items() if k != "observations"},
}

TEACHERS = [{"id": 1, "email": "ana.garcia@educa.madrid.org"}, {"id": 2, "email": "luis.perez@educa.madrid.org"}]
# 2026-03-05 is a Thursday.
TIMESLOTS = [
    {"id": 10, "weekday": "Thursday", "period_number": 3, "start_time": "11:10:00", "end_time": "12:05:00"},
    {"id": 11, "weekday": "Tuesday", "period_number": 1, "start_time": "08:25:00", "end_time": "09:20:00"},
]


def make_row(**overrides):
    row = {
        "id": "1",
        "submitdate": "2026-03-05 10:00:00",
        "Q01": "ana.garcia",
        "Q02": "2026-03-05",
        "Q03": "11:10-12:05",
        "Q04": "2ESO A",
        "Q05": "101",
        "Q06": "Ejercicios del libro, tema 4",
        "Q07": "",
    }
    row.update(overrides)
    return row


def test_build_teacher_lookup_uses_email_local_part():
    lookup = build_teacher_lookup(TEACHERS)
    assert lookup == {"ana.garcia": 1, "luis.perez": 2}


def test_build_timeslot_lookup_keys_by_weekday_and_hour_range():
    lookup = build_timeslot_lookup(TIMESLOTS)
    assert lookup[("Thursday", "11:10-12:05")] == 10
    assert lookup[("Tuesday", "8:25-9:20")] == 11


def test_happy_path_mapping_derives_weekday_from_date():
    teacher_lookup = build_teacher_lookup(TEACHERS)
    timeslot_lookup = build_timeslot_lookup(TIMESLOTS)
    payload = map_response_to_absence(make_row(), FIELD_MAPPING, teacher_lookup, timeslot_lookup)
    assert payload == {
        "date": "2026-03-05",
        "timeslot_id": 10,
        "absent_teacher_id": 1,
        "class_group": "2ESO A",
        "classroom": "101",
        "task_left": "Ejercicios del libro, tema 4",
        "observations": None,
    }


def test_unknown_teacher_raises():
    teacher_lookup = build_teacher_lookup(TEACHERS)
    timeslot_lookup = build_timeslot_lookup(TIMESLOTS)
    row = make_row(Q01="desconocido")
    with pytest.raises(MappingError, match="No teacher found"):
        map_response_to_absence(row, FIELD_MAPPING, teacher_lookup, timeslot_lookup)


def test_unknown_hour_range_raises():
    teacher_lookup = build_teacher_lookup(TEACHERS)
    timeslot_lookup = build_timeslot_lookup(TIMESLOTS)
    row = make_row(Q03="16:00-16:55")
    with pytest.raises(MappingError, match="No timeslot found"):
        map_response_to_absence(row, FIELD_MAPPING, teacher_lookup, timeslot_lookup)


def test_weekend_date_raises():
    teacher_lookup = build_teacher_lookup(TEACHERS)
    timeslot_lookup = build_timeslot_lookup(TIMESLOTS)
    row = make_row(Q02="2026-03-07")  # a Saturday
    with pytest.raises(MappingError, match="weekend"):
        map_response_to_absence(row, FIELD_MAPPING, teacher_lookup, timeslot_lookup)


def test_missing_task_left_raises():
    teacher_lookup = build_teacher_lookup(TEACHERS)
    timeslot_lookup = build_timeslot_lookup(TIMESLOTS)
    row = make_row(Q06="")
    with pytest.raises(MappingError):
        map_response_to_absence(row, FIELD_MAPPING, teacher_lookup, timeslot_lookup)


def test_observations_present_is_kept():
    teacher_lookup = build_teacher_lookup(TEACHERS)
    timeslot_lookup = build_timeslot_lookup(TIMESLOTS)
    row = make_row(Q07="Avisar con antelación")
    payload = map_response_to_absence(row, FIELD_MAPPING, teacher_lookup, timeslot_lookup)
    assert payload["observations"] == "Avisar con antelación"


def test_observations_column_can_be_omitted_from_mapping():
    teacher_lookup = build_teacher_lookup(TEACHERS)
    timeslot_lookup = build_timeslot_lookup(TIMESLOTS)
    payload = map_response_to_absence(make_row(), FIELD_MAPPING_NO_OBSERVATIONS, teacher_lookup, timeslot_lookup)
    assert payload["observations"] is None


def test_is_response_complete():
    assert is_response_complete({"submitdate": "2026-03-05 10:00:00"}) is True
    assert is_response_complete({"submitdate": ""}) is False
    assert is_response_complete({}) is False


@pytest.mark.parametrize("value", ["N/A", "  ", None])
def test_is_response_complete_treats_na_and_blank_as_incomplete(value):
    assert is_response_complete({"submitdate": value}) is False


def test_is_response_complete_accepts_the_spanish_export_header():
    assert is_response_complete({"Fecha de envío": "2026-03-05 10:00:00"}) is True


# --- load_field_mapping -------------------------------------------------------


def test_load_field_mapping_reads_json_from_disk(tmp_path):
    path = tmp_path / "field_mapping.json"
    path.write_text(json.dumps(FIELD_MAPPING), encoding="utf-8")
    assert load_field_mapping(path) == FIELD_MAPPING


# --- build_teacher_lookup / build_timeslot_lookup: empty inputs ---------------


def test_build_teacher_lookup_with_no_teachers_is_empty():
    assert build_teacher_lookup([]) == {}


def test_build_timeslot_lookup_with_no_timeslots_is_empty():
    assert build_timeslot_lookup([]) == {}


# --- map_response_to_absence: malformed / negative inputs ---------------------


@pytest.mark.parametrize("bad_username", ["", "   "])
def test_empty_username_raises(bad_username):
    teacher_lookup = build_teacher_lookup(TEACHERS)
    timeslot_lookup = build_timeslot_lookup(TIMESLOTS)
    row = make_row(Q01=bad_username)
    with pytest.raises(MappingError, match="no username"):
        map_response_to_absence(row, FIELD_MAPPING, teacher_lookup, timeslot_lookup)


@pytest.mark.parametrize("bad_date", ["not-a-date", "2026-13-45", "05-03-2026", ""])
def test_unrecognized_date_format_raises(bad_date):
    teacher_lookup = build_teacher_lookup(TEACHERS)
    timeslot_lookup = build_timeslot_lookup(TIMESLOTS)
    row = make_row(Q02=bad_date)
    with pytest.raises(MappingError, match="date format"):
        map_response_to_absence(row, FIELD_MAPPING, teacher_lookup, timeslot_lookup)


@pytest.mark.parametrize("raw, expected", [
    ("2026-03-05", "2026-03-05"),
    ("05.03.2026", "2026-03-05"),
    ("05/03/2026", "2026-03-05"),
    ("2026-03-05 00:00:00", "2026-03-05"),
])
def test_all_supported_date_formats_parse_to_the_same_date(raw, expected):
    teacher_lookup = build_teacher_lookup(TEACHERS)
    timeslot_lookup = build_timeslot_lookup(TIMESLOTS)
    row = make_row(Q02=raw)
    payload = map_response_to_absence(row, FIELD_MAPPING, teacher_lookup, timeslot_lookup)
    assert payload["date"] == expected


@pytest.mark.parametrize("bad_period", ["11:10", "11:10-12:05-13:00", "", "just wrong"])
def test_malformed_hour_range_raises(bad_period):
    teacher_lookup = build_teacher_lookup(TEACHERS)
    timeslot_lookup = build_timeslot_lookup(TIMESLOTS)
    row = make_row(Q03=bad_period)
    with pytest.raises(MappingError, match="hour range"):
        map_response_to_absence(row, FIELD_MAPPING, teacher_lookup, timeslot_lookup)


@pytest.mark.parametrize("bad_period", ["1110-1205", "ab:cd-9:20", "11:10-ab:cd"])
def test_malformed_time_token_raises(bad_period):
    teacher_lookup = build_teacher_lookup(TEACHERS)
    timeslot_lookup = build_timeslot_lookup(TIMESLOTS)
    row = make_row(Q03=bad_period)
    with pytest.raises(MappingError, match="time value"):
        map_response_to_absence(row, FIELD_MAPPING, teacher_lookup, timeslot_lookup)


def test_missing_column_mapping_for_a_required_field_raises():
    teacher_lookup = build_teacher_lookup(TEACHERS)
    timeslot_lookup = build_timeslot_lookup(TIMESLOTS)
    incomplete_mapping = {
        "response_id_column": "id",
        "columns": {k: v for k, v in FIELD_MAPPING["columns"].items() if k != "period"},
    }
    with pytest.raises(MappingError, match="no column configured for 'period'"):
        map_response_to_absence(make_row(), incomplete_mapping, teacher_lookup, timeslot_lookup)


def test_html_or_corrupted_payload_in_a_text_field_is_kept_verbatim_not_executed():
    """task_left/observations are free text; anything a respondent typed - even
    HTML/script-like content - must pass through unmodified, never be evaluated
    or stripped as if it were markup."""
    teacher_lookup = build_teacher_lookup(TEACHERS)
    timeslot_lookup = build_timeslot_lookup(TIMESLOTS)
    payload_text = "<script>alert(1)</script> & weird \"quotes\" 'n stuff"
    row = make_row(Q06=payload_text)
    payload = map_response_to_absence(row, FIELD_MAPPING, teacher_lookup, timeslot_lookup)
    assert payload["task_left"] == payload_text
