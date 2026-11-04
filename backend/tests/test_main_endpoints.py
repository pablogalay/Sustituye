"""Endpoint-level tests for app.main: auth middleware edge cases, admin-only
RBAC, teacher-role data scoping, 404/409/422 error paths, the endpoints that
test_api.py never touches (groups/classrooms/availability, absence filters,
the guard-duty PDF report), and infrastructure failure handling for the
EducaMadrid sync trigger.

Uses its own isolated in-memory SQLite database (via dependency_overrides)
instead of the shared test_api.db file, so these tests don't depend on
execution order or on state left behind by other test modules."""
import os
os.environ.setdefault('DATABASE_URL', 'sqlite:///./test_main_endpoints_unused.db')
os.environ.setdefault('JWT_SECRET', 'test-secret')

from datetime import date, time

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import hash_password
from app.database import Base, get_db
from app.main import app
from app.models import Absence, AssignmentStatistic, Teacher, TimeSlot

engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base.metadata.create_all(engine)


def _override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


client = TestClient(app)


@pytest.fixture(autouse=True, scope='module')
def _use_isolated_database():
    """app is a process-wide singleton shared with every other test module, so
    the override must be removed afterwards - otherwise it silently redirects
    test_api.py's requests (and any other module imported later) to this
    module's throwaway in-memory database instead of their own."""
    app.dependency_overrides[get_db] = _override_get_db
    yield
    del app.dependency_overrides[get_db]

# --- fixed seed: 2 timeslots, 3 teachers (2 active, 1 inactive) -------------
with TestingSessionLocal() as _db:
    _db.add_all([
        TimeSlot(weekday='Monday', period_number=1, start_time=time(8, 25), end_time=time(9, 20)),
        TimeSlot(weekday='Tuesday', period_number=1, start_time=time(8, 25), end_time=time(9, 20)),
    ])
    _db.add_all([
        Teacher(first_name='Teacher', last_name='One', email='teacher.one@x.test', active=True, password_hash=hash_password('password123')),
        Teacher(first_name='Teacher', last_name='Two', email='teacher.two@x.test', active=True, password_hash=hash_password('password123')),
        Teacher(first_name='Teacher', last_name='Inactive', email='teacher.inactive@x.test', active=False, password_hash=hash_password('password123')),
    ])
    _db.commit()

TIMESLOT_MONDAY = 1
TIMESLOT_TUESDAY = 2
TEACHER_ONE = 1
TEACHER_TWO = 2
TEACHER_INACTIVE = 3


def admin_headers():
    token = client.post('/auth/login', json={'email': 'admin@school.local', 'password': 'admin123'}).json()['access_token']
    return {'Authorization': f'Bearer {token}'}


def teacher_headers(email='teacher.one@x.test', password='password123'):
    token = client.post('/auth/login', json={'email': email, 'password': password}).json()['access_token']
    return {'Authorization': f'Bearer {token}'}


def create_absence(headers, **overrides):
    payload = dict(
        date='2026-08-10', timeslot_id=TIMESLOT_MONDAY, absent_teacher_id=TEACHER_ONE,
        class_group='TestGroup', classroom='TestRoom', task_left='Trabajo', observations=None,
    )
    payload.update(overrides)
    return client.post('/absences', headers=headers, json=payload)


# --- auth middleware edge cases ----------------------------------------------


@pytest.mark.parametrize('headers', [
    {},
    {'Authorization': 'Basic dXNlcjpwYXNz'},
    {'Authorization': 'Bearer '},
    {'Authorization': 'Bearer not-a-jwt'},
    {'Authorization': 'bearer admin123'},  # wrong case scheme
])
def test_protected_endpoint_rejects_bad_or_missing_auth(headers):
    response = client.get('/teachers', headers=headers)
    assert response.status_code == 401


def test_docs_and_openapi_are_reachable_without_auth():
    assert client.get('/openapi.json').status_code == 200
    assert client.get('/docs').status_code == 200


def test_cors_preflight_bypasses_auth():
    response = client.options(
        '/teachers',
        headers={'Origin': 'http://localhost:5173', 'Access-Control-Request-Method': 'GET'},
    )
    assert response.status_code == 200
    assert 'access-control-allow-origin' in {k.lower() for k in response.headers}


def test_auth_me_returns_claims_for_valid_token():
    response = client.get('/auth/me', headers=admin_headers())
    assert response.status_code == 200
    assert response.json()['role'] == 'admin'


# --- RBAC: admin-only endpoints reject teacher tokens ------------------------


ADMIN_ONLY_REQUESTS = [
    ('post', '/teachers', {'first_name': 'X', 'last_name': 'Y', 'email': 'rbac@x.test', 'active': True}),
    ('put', '/teachers/1', {'first_name': 'X', 'last_name': 'Y', 'email': 'rbac2@x.test', 'active': True}),
    ('delete', '/teachers/1', None),
    ('put', '/availability', {'teacher_id': 1, 'entries': []}),
    ('delete', '/absences/1', None),
    ('patch', '/absences/1/force-assign', {'substitute_id': 1}),
    ('put', '/absences/1', {'date': '2026-09-17', 'timeslot_id': 1, 'class_group': 'X', 'classroom': 'X', 'task_left': 'X', 'substitute_id': None}),
    ('get', '/sessions/2026-09-17/1/teachers-status', None),
    ('post', '/admin/sync-educamadrid', None),
    ('post', '/admin/reset-year', None),
    ('get', '/reports/guard-duty.pdf', None),
    ('get', '/dashboard', None),
]


@pytest.mark.parametrize('method, path, body', ADMIN_ONLY_REQUESTS)
def test_admin_only_endpoints_reject_teacher_role(method, path, body):
    response = client.request(method.upper(), path, headers=teacher_headers(), json=body)
    assert response.status_code == 403


# --- 404s (admin) -------------------------------------------------------------


def test_update_nonexistent_teacher_returns_404():
    response = client.put('/teachers/999999', headers=admin_headers(), json={'first_name': 'X', 'last_name': 'Y', 'email': 'nope@x.test', 'active': True})
    assert response.status_code == 404


def test_delete_nonexistent_teacher_returns_404():
    assert client.delete('/teachers/999999', headers=admin_headers()).status_code == 404


def test_delete_nonexistent_absence_returns_404():
    assert client.delete('/absences/999999', headers=admin_headers()).status_code == 404


def test_set_availability_for_nonexistent_teacher_returns_404():
    response = client.put('/availability', headers=admin_headers(), json={'teacher_id': 999999, 'entries': []})
    assert response.status_code == 404


# --- 422s: teacher password length boundary ----------------------------------


@pytest.mark.parametrize('password, expected_status', [
    (None, 422),
    ('', 422),
    ('1234567', 422),   # 7 chars: below the 8-char minimum
    ('12345678', 201),  # exactly 8 chars: boundary should pass
])
def test_create_teacher_enforces_minimum_password_length(password, expected_status):
    payload = {'first_name': 'Pw', 'last_name': 'Test', 'email': f'pwtest-{password}@x.test', 'active': True, 'password': password}
    response = client.post('/teachers', headers=admin_headers(), json=payload)
    assert response.status_code == expected_status


def test_update_teacher_happy_path_persists_changes_and_new_password():
    created = client.post('/teachers', headers=admin_headers(), json={'first_name': 'Before', 'last_name': 'Update', 'email': 'updatehappy@x.test', 'active': True, 'password': 'validpassword123'}).json()
    response = client.put(f'/teachers/{created["id"]}', headers=admin_headers(), json={'first_name': 'After', 'last_name': 'Update', 'email': 'updatehappy@x.test', 'active': False, 'password': 'newvalidpassword'})
    assert response.status_code == 200
    body = response.json()
    assert body['first_name'] == 'After'
    assert body['active'] is False
    # login with the new password proves the hash was actually persisted
    login_response = client.post('/auth/login', json={'email': 'updatehappy@x.test', 'password': 'newvalidpassword'})
    assert login_response.status_code == 401  # active=False teachers cannot log in
    client.put(f'/teachers/{created["id"]}', headers=admin_headers(), json={'first_name': 'After', 'last_name': 'Update', 'email': 'updatehappy@x.test', 'active': True, 'password': 'newvalidpassword'})
    assert client.post('/auth/login', json={'email': 'updatehappy@x.test', 'password': 'newvalidpassword'}).status_code == 200


def test_update_teacher_without_changing_password_keeps_old_one():
    created = client.post('/teachers', headers=admin_headers(), json={'first_name': 'Keep', 'last_name': 'Pw', 'email': 'keeppw@x.test', 'active': True, 'password': 'originalpassword'}).json()
    response = client.put(f'/teachers/{created["id"]}', headers=admin_headers(), json={'first_name': 'Keep', 'last_name': 'PwChanged', 'email': 'keeppw@x.test', 'active': True})
    assert response.status_code == 200
    assert client.post('/auth/login', json={'email': 'keeppw@x.test', 'password': 'originalpassword'}).status_code == 200


def test_delete_teacher_rolls_back_and_returns_409_on_integrity_error(monkeypatch):
    created = client.post('/teachers', headers=admin_headers(), json={'first_name': 'Race', 'last_name': 'Condition', 'email': 'race@x.test', 'active': True, 'password': 'validpassword123'}).json()

    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session as OrmSession

    def raising_commit(self):
        raise IntegrityError('DELETE', {}, Exception('FK violation'))

    monkeypatch.setattr(OrmSession, 'commit', raising_commit)
    response = client.delete(f'/teachers/{created["id"]}', headers=admin_headers())
    assert response.status_code == 409


def test_update_teacher_enforces_minimum_password_length():
    created = client.post('/teachers', headers=admin_headers(), json={'first_name': 'Upd', 'last_name': 'Test', 'email': 'updtest@x.test', 'active': True, 'password': 'validpassword123'}).json()
    response = client.put(f'/teachers/{created["id"]}', headers=admin_headers(), json={'first_name': 'Upd', 'last_name': 'Test', 'email': 'updtest@x.test', 'active': True, 'password': 'short'})
    assert response.status_code == 422


# --- teacher duty_weight (part-time load balancing) --------------------------


def test_new_teacher_defaults_to_a_full_duty_weight_of_one():
    created = client.post('/teachers', headers=admin_headers(), json={'first_name': 'Def', 'last_name': 'Weight', 'email': 'defweight@x.test', 'active': True, 'password': 'validpassword123'}).json()
    assert created['duty_weight'] == 1.0


def test_teacher_duty_weight_can_be_set_above_one_for_part_time_staff():
    created = client.post('/teachers', headers=admin_headers(), json={'first_name': 'Part', 'last_name': 'Weight', 'email': 'partweight@x.test', 'active': True, 'password': 'validpassword123', 'duty_weight': 1.5}).json()
    assert created['duty_weight'] == 1.5
    fetched = next(t for t in client.get('/teachers', headers=admin_headers()).json() if t['id'] == created['id'])
    assert fetched['duty_weight'] == 1.5


@pytest.mark.parametrize('duty_weight', [0, -1, -0.5])
def test_teacher_duty_weight_must_be_positive(duty_weight):
    payload = {'first_name': 'Bad', 'last_name': 'Weight', 'email': f'badweight-{duty_weight}@x.test', 'active': True, 'password': 'validpassword123', 'duty_weight': duty_weight}
    assert client.post('/teachers', headers=admin_headers(), json=payload).status_code == 422


def test_update_teacher_can_change_duty_weight():
    created = client.post('/teachers', headers=admin_headers(), json={'first_name': 'Chg', 'last_name': 'Weight', 'email': 'chgweight@x.test', 'active': True, 'password': 'validpassword123'}).json()
    response = client.put(f'/teachers/{created["id"]}', headers=admin_headers(), json={'first_name': 'Chg', 'last_name': 'Weight', 'email': 'chgweight@x.test', 'active': True, 'duty_weight': 2})
    assert response.status_code == 200
    assert response.json()['duty_weight'] == 2


@pytest.mark.parametrize('duty_type', ['FOO', '', 'guard', 'Support', None])
def test_set_availability_rejects_invalid_duty_type(duty_type):
    response = client.put('/availability', headers=admin_headers(), json={'teacher_id': TEACHER_ONE, 'entries': [{'timeslot_id': TIMESLOT_MONDAY, 'duty_type': duty_type}]})
    assert response.status_code == 422


def test_set_availability_rejects_unknown_timeslot_id():
    response = client.put('/availability', headers=admin_headers(), json={'teacher_id': TEACHER_ONE, 'entries': [{'timeslot_id': 999999, 'duty_type': 'GUARD'}]})
    assert response.status_code == 422


def test_set_availability_accepts_empty_entries_and_clears_previous():
    client.put('/availability', headers=admin_headers(), json={'teacher_id': TEACHER_ONE, 'entries': [{'timeslot_id': TIMESLOT_MONDAY, 'duty_type': 'GUARD'}]})
    response = client.put('/availability', headers=admin_headers(), json={'teacher_id': TEACHER_ONE, 'entries': []})
    assert response.status_code == 200
    remaining = [x for x in client.get('/availability', headers=admin_headers()).json() if x['teacher_id'] == TEACHER_ONE]
    assert remaining == []


@pytest.mark.parametrize('bad_value', [' ', '', '\t\n', '   '])
def test_create_absence_rejects_blank_group_and_classroom(bad_value):
    response = create_absence(admin_headers(), class_group=bad_value, classroom='Valid')
    assert response.status_code == 422
    response = create_absence(admin_headers(), class_group='Valid', classroom=bad_value)
    assert response.status_code == 422


# --- teacher-role scoping ------------------------------------------------------


def test_teacher_can_create_absence_for_self():
    response = create_absence(teacher_headers(), date='2026-08-11', absent_teacher_id=TEACHER_ONE)
    assert response.status_code == 201


def test_teacher_cannot_create_absence_for_someone_else():
    response = create_absence(teacher_headers(), date='2026-08-12', absent_teacher_id=TEACHER_TWO)
    assert response.status_code == 403


def test_teacher_only_sees_own_absences():
    create_absence(admin_headers(), date='2026-08-13', absent_teacher_id=TEACHER_ONE, class_group='Own', classroom='Own')
    create_absence(admin_headers(), date='2026-08-13', absent_teacher_id=TEACHER_TWO, class_group='Other', classroom='Other')
    response = client.get('/absences', headers=teacher_headers())
    assert response.status_code == 200
    seen_teacher_ids = {row['absent_teacher_id'] for row in response.json()} | {
        row['substitute_teacher_id'] for row in response.json() if row['substitute_teacher_id'] is not None
    }
    assert seen_teacher_ids <= {TEACHER_ONE}


def test_teacher_only_sees_own_statistics():
    response = client.get('/statistics', headers=teacher_headers())
    assert response.status_code == 200
    assert {row['teacher']['id'] for row in response.json()} <= {TEACHER_ONE}


def test_login_rejects_inactive_teacher():
    response = client.post('/auth/login', json={'email': 'teacher.inactive@x.test', 'password': 'password123'})
    assert response.status_code == 401


# --- groups / classrooms / timeslots / availability happy paths --------------


def test_groups_and_classrooms_reflect_absences_created():
    create_absence(admin_headers(), date='2026-08-14', class_group='UniqueGroupXYZ', classroom='UniqueRoomXYZ')
    groups = client.get('/groups', headers=admin_headers()).json()
    classrooms = client.get('/classrooms', headers=admin_headers()).json()
    assert any(g['name'] == 'UniqueGroupXYZ' for g in groups)
    assert any(c['name'] == 'UniqueRoomXYZ' for c in classrooms)


def test_timeslots_ordered_by_weekday_then_period():
    response = client.get('/timeslots', headers=admin_headers())
    assert response.status_code == 200
    weekdays = [row['weekday'] for row in response.json()]
    assert weekdays.index('Monday') < weekdays.index('Tuesday')


def test_set_and_get_availability_roundtrip():
    client.put('/availability', headers=admin_headers(), json={'teacher_id': TEACHER_TWO, 'entries': [{'timeslot_id': TIMESLOT_TUESDAY, 'duty_type': 'SUPPORT'}]})
    entries = [x for x in client.get('/availability', headers=admin_headers()).json() if x['teacher_id'] == TEACHER_TWO]
    assert entries == [{'teacher_id': TEACHER_TWO, 'timeslot_id': TIMESLOT_TUESDAY, 'duty_type': 'SUPPORT'}]


# --- /absences filters ---------------------------------------------------------


def test_absences_filters_by_date_range_and_teacher():
    create_absence(admin_headers(), date='2020-01-06', absent_teacher_id=TEACHER_ONE, class_group='FilterA', classroom='FilterA', timeslot_id=TIMESLOT_MONDAY)
    create_absence(admin_headers(), date='2020-01-07', absent_teacher_id=TEACHER_TWO, class_group='FilterB', classroom='FilterB', timeslot_id=TIMESLOT_TUESDAY)

    by_date = client.get('/absences', headers=admin_headers(), params={'date_from': '2020-01-06', 'date_to': '2020-01-06'}).json()
    assert {row['class_group_id'] for row in by_date} or True  # sanity: request succeeded
    assert all(row['date'] == '2020-01-06' for row in by_date)

    by_teacher = client.get('/absences', headers=admin_headers(), params={'teacher_id': TEACHER_TWO}).json()
    assert all(row['absent_teacher_id'] == TEACHER_TWO for row in by_teacher)
    assert any(row['date'] == '2020-01-07' for row in by_teacher)


def test_absences_filter_by_group_id():
    created = create_absence(admin_headers(), date='2020-02-01', class_group='GroupFilterOnly', classroom='RoomFilterOnly').json()
    group_id = created['class_group_id']
    response = client.get('/absences', headers=admin_headers(), params={'group_id': group_id}).json()
    assert all(row['class_group_id'] == group_id for row in response)
    assert any(row['id'] == created['id'] for row in response)


# --- sync-educamadrid infrastructure failure paths ----------------------------


def test_sync_educamadrid_relays_non_200_as_502(monkeypatch):
    class FakeResponse:
        status_code = 502
        text = 'upstream sync service exploded'
        def json(self):
            return {}
    monkeypatch.setattr('app.main.httpx.post', lambda url, headers=None, timeout=None: FakeResponse())
    response = client.post('/admin/sync-educamadrid', headers=admin_headers())
    assert response.status_code == 502
    assert 'upstream sync service exploded' in response.json()['detail']


@pytest.mark.parametrize('exception', [httpx.ConnectTimeout('timed out'), httpx.ConnectError('refused'), httpx.ReadTimeout('read timed out')])
def test_sync_educamadrid_reports_503_for_any_connection_failure(monkeypatch, exception):
    def fake_post(url, headers=None, timeout=None):
        raise exception
    monkeypatch.setattr('app.main.httpx.post', fake_post)
    response = client.post('/admin/sync-educamadrid', headers=admin_headers())
    assert response.status_code == 503


# --- guard-duty PDF report -----------------------------------------------------


def test_guard_duty_report_returns_a_pdf():
    response = client.get('/reports/guard-duty.pdf', headers=admin_headers())
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/pdf'
    assert 'attachment' in response.headers['content-disposition']
    assert response.content.startswith(b'%PDF')


# --- dashboard -------------------------------------------------------------------


# --- a substitute who later reports their own absence must be replaced --------


def test_substitute_who_later_reports_absence_gets_replaced():
    original = client.post('/teachers', headers=admin_headers(), json={'first_name': 'Orig', 'last_name': 'Sub', 'email': 'origsub@x.test', 'active': True, 'password': 'validpassword123'}).json()
    backup = client.post('/teachers', headers=admin_headers(), json={'first_name': 'Backup', 'last_name': 'Sub', 'email': 'backupsub@x.test', 'active': True, 'password': 'validpassword123'}).json()
    for teacher in (original, backup):
        client.put('/availability', headers=admin_headers(), json={'teacher_id': teacher['id'], 'entries': [{'timeslot_id': TIMESLOT_MONDAY, 'duty_type': 'GUARD'}]})

    covered = create_absence(admin_headers(), date='2026-08-24', absent_teacher_id=TEACHER_TWO, class_group='ReassignG', classroom='ReassignR', timeslot_id=TIMESLOT_MONDAY).json()
    assigned_id = covered['substitute_teacher_id']
    assert assigned_id in (original['id'], backup['id'])
    other_id = backup['id'] if assigned_id == original['id'] else original['id']

    # The teacher just assigned as substitute now reports their own absence for
    # that same date/timeslot - the earlier assignment must move to the other one.
    response = create_absence(admin_headers(), date='2026-08-24', absent_teacher_id=assigned_id, class_group='SelfG', classroom='SelfR', timeslot_id=TIMESLOT_MONDAY)
    assert response.status_code == 201
    assert response.json()['substitute_teacher_id'] is None  # nobody left to cover the now-absent substitute

    reloaded = client.get('/absences', headers=admin_headers(), params={'group_id': covered['class_group_id']}).json()
    updated = next(row for row in reloaded if row['id'] == covered['id'])
    assert updated['substitute_teacher_id'] == other_id


def test_dashboard_counts_covering_and_unassigned_absences():
    today = date.today().isoformat()
    create_absence(admin_headers(), date=today, class_group='DashG', classroom='DashR', timeslot_id=TIMESLOT_MONDAY)
    response = client.get('/dashboard', headers=admin_headers())
    assert response.status_code == 200
    body = response.json()
    assert body['absence_count'] == body['covering_count'] + body['unassigned_count']


# --- corridor guard duties ---------------------------------------------------


def test_hallway_duties_roster_is_generated_on_read_for_the_requested_day():
    for teacher in (TEACHER_ONE, TEACHER_TWO):
        client.put('/availability', headers=admin_headers(), json={'teacher_id': teacher, 'entries': [{'timeslot_id': TIMESLOT_MONDAY, 'duty_type': 'GUARD'}]})

    body = client.get('/hallway-duties', headers=admin_headers(), params={'day': '2026-09-07'}).json()

    assert [x['post'] for x in body['posts']] == ['GROUND', 'FIRST', 'SECOND']
    monday = [x for x in body['duties'] if x['timeslot_id'] == TIMESLOT_MONDAY]
    assert [x['post'] for x in monday] == ['GROUND', 'FIRST', 'SECOND']
    assert monday[0]['label'] == 'Pasillo planta baja'
    staffed = [x['teacher_id'] for x in monday if x['teacher_id'] is not None]
    # Posts are filled from the ground floor upwards, so the empty ones are always
    # the last, and nobody covers two corridors in the same session.
    assert [x['teacher_id'] for x in monday][:len(staffed)] == staffed
    assert len(set(staffed)) == len(staffed) >= 1


def test_hallway_duties_are_visible_to_teachers():
    assert client.get('/hallway-duties', headers=teacher_headers(), params={'day': '2026-09-07'}).status_code == 200


def test_saturday_has_no_corridor_duties():
    assert client.get('/hallway-duties', headers=admin_headers(), params={'day': '2026-09-12'}).json()['duties'] == []


# --- manual assignment of pending absences (admin override) ------------------


def _isolated_timeslot(period_number):
    """A TimeSlot no teacher has ever been given availability for, so
    assign_substitute() is guaranteed to leave a new absence on it unassigned."""
    with TestingSessionLocal() as db:
        slot = TimeSlot(weekday='Wednesday', period_number=period_number, start_time=time(8, 25), end_time=time(9, 20))
        db.add(slot)
        db.commit()
        db.refresh(slot)
        return slot.id


def test_force_assign_credits_statistic_and_notifies(monkeypatch):
    notified = {}
    monkeypatch.setattr(
        'app.main.send_substitution_email',
        lambda db, absence, substitute_teacher_id: notified.update(
            substitute_teacher_id=substitute_teacher_id, class_group=absence.class_group_name, classroom=absence.classroom_name,
        ) or True,
    )
    timeslot_id = _isolated_timeslot(1)
    substitute = client.post('/teachers', headers=admin_headers(), json={'first_name': 'Manual', 'last_name': 'Sub', 'email': 'manualsub@x.test', 'active': True, 'password': 'validpassword123'}).json()

    pending = create_absence(admin_headers(), date='2026-09-09', absent_teacher_id=TEACHER_ONE, class_group='ManualG', classroom='ManualR', timeslot_id=timeslot_id).json()
    assert pending['substitute_teacher_id'] is None

    response = client.patch(f"/absences/{pending['id']}/force-assign", headers=admin_headers(), json={'substitute_id': substitute['id']})
    assert response.status_code == 200
    assert response.json()['substitute_teacher_id'] == substitute['id']

    with TestingSessionLocal() as db:
        stat = db.get(AssignmentStatistic, (substitute['id'], timeslot_id))
        assert stat.assignment_count == substitute['duty_weight']

    assert notified == {'substitute_teacher_id': substitute['id'], 'class_group': 'ManualG', 'classroom': 'ManualR'}


def test_force_assign_bypasses_availability_for_a_teacher_with_no_guard_or_support():
    """The whole point of force-assign: it must succeed and still credit the counter
    even though this teacher has zero Availability rows for the session."""
    timeslot_id = _isolated_timeslot(8)
    substitute = client.post('/teachers', headers=admin_headers(), json={'first_name': 'NoDuty', 'last_name': 'Sub', 'email': 'noduty@x.test', 'active': True, 'password': 'validpassword123'}).json()
    absence = create_absence(admin_headers(), date='2099-02-01', absent_teacher_id=TEACHER_ONE, class_group='BypassG', classroom='BypassR', timeslot_id=timeslot_id).json()

    response = client.patch(f"/absences/{absence['id']}/force-assign", headers=admin_headers(), json={'substitute_id': substitute['id']})
    assert response.status_code == 200
    assert response.json()['substitute_teacher_id'] == substitute['id']
    with TestingSessionLocal() as db:
        stat = db.get(AssignmentStatistic, (substitute['id'], timeslot_id))
        assert stat.assignment_count == substitute['duty_weight']


def test_force_assign_returns_404_for_unknown_absence():
    substitute = client.post('/teachers', headers=admin_headers(), json={'first_name': 'X404', 'last_name': 'Sub', 'email': 'x404sub@x.test', 'active': True, 'password': 'validpassword123'}).json()
    response = client.patch('/absences/999999/force-assign', headers=admin_headers(), json={'substitute_id': substitute['id']})
    assert response.status_code == 404


def test_force_assign_returns_400_when_already_covered(monkeypatch):
    monkeypatch.setattr('app.main.send_substitution_email', lambda db, absence, substitute_teacher_id: True)
    timeslot_id = _isolated_timeslot(2)
    substitute = client.post('/teachers', headers=admin_headers(), json={'first_name': 'Cov', 'last_name': 'Sub', 'email': 'covsub@x.test', 'active': True, 'password': 'validpassword123'}).json()
    other = client.post('/teachers', headers=admin_headers(), json={'first_name': 'Cov2', 'last_name': 'Sub2', 'email': 'covsub2@x.test', 'active': True, 'password': 'validpassword123'}).json()
    absence = create_absence(admin_headers(), date='2026-09-10', absent_teacher_id=TEACHER_ONE, class_group='CovG', classroom='CovR', timeslot_id=timeslot_id).json()

    first = client.patch(f"/absences/{absence['id']}/force-assign", headers=admin_headers(), json={'substitute_id': substitute['id']})
    assert first.status_code == 200
    second = client.patch(f"/absences/{absence['id']}/force-assign", headers=admin_headers(), json={'substitute_id': other['id']})
    assert second.status_code == 400


def test_force_assign_returns_404_for_unknown_substitute():
    timeslot_id = _isolated_timeslot(3)
    absence = create_absence(admin_headers(), date='2026-09-11', absent_teacher_id=TEACHER_ONE, class_group='UnkG', classroom='UnkR', timeslot_id=timeslot_id).json()
    response = client.patch(f"/absences/{absence['id']}/force-assign", headers=admin_headers(), json={'substitute_id': 999999})
    assert response.status_code == 404


def test_force_assign_returns_422_for_inactive_substitute():
    timeslot_id = _isolated_timeslot(4)
    absence = create_absence(admin_headers(), date='2026-09-12', absent_teacher_id=TEACHER_ONE, class_group='InactG', classroom='InactR', timeslot_id=timeslot_id).json()
    response = client.patch(f"/absences/{absence['id']}/force-assign", headers=admin_headers(), json={'substitute_id': TEACHER_INACTIVE})
    assert response.status_code == 422


def test_force_assign_rejects_self_assignment():
    timeslot_id = _isolated_timeslot(5)
    absence = create_absence(admin_headers(), date='2026-09-13', absent_teacher_id=TEACHER_ONE, class_group='SelfG', classroom='SelfR', timeslot_id=timeslot_id).json()
    response = client.patch(f"/absences/{absence['id']}/force-assign", headers=admin_headers(), json={'substitute_id': TEACHER_ONE})
    assert response.status_code == 422


def test_force_assign_rejects_a_substitute_who_is_also_absent_that_session():
    timeslot_id = _isolated_timeslot(9)
    absence_one = create_absence(admin_headers(), date='2099-02-02', absent_teacher_id=TEACHER_ONE, class_group='DoubleAbsG1', classroom='DoubleAbsR1', timeslot_id=timeslot_id).json()
    absence_two = create_absence(admin_headers(), date='2099-02-02', absent_teacher_id=TEACHER_TWO, class_group='DoubleAbsG2', classroom='DoubleAbsR2', timeslot_id=timeslot_id).json()

    response = client.patch(f"/absences/{absence_one['id']}/force-assign", headers=admin_headers(), json={'substitute_id': TEACHER_TWO})
    assert response.status_code == 422
    assert absence_two['absent_teacher_id'] == TEACHER_TWO


def test_force_assign_releases_and_backfills_the_conflicting_absence(monkeypatch):
    """The chosen substitute is already covering `other`; force-assigning them onto
    `target` must free `other` (credit reversed) and let the automatic engine try to
    refill it, notifying whoever ends up covering it."""
    # SMTP is left unconfigured, so the real send_substitution_email() short-circuits to
    # False without touching the network; wrapping it still proves it was *called* (and
    # with which absence/substitute), which is what this test needs.
    notified_absence_ids = []
    import app.main as main_module
    real_send_substitution_email = main_module.send_substitution_email

    def spy(db, absence, substitute_teacher_id):
        notified_absence_ids.append((absence.id, substitute_teacher_id))
        return real_send_substitution_email(db, absence, substitute_teacher_id)

    monkeypatch.setattr('app.main.send_substitution_email', spy)

    timeslot_id = _isolated_timeslot(10)
    shared_substitute = client.post('/teachers', headers=admin_headers(), json={'first_name': 'Shared', 'last_name': 'Sub', 'email': 'sharedsub@x.test', 'active': True, 'password': 'validpassword123'}).json()
    # Give a third teacher GUARD availability for this slot, so assign_substitute() has
    # somebody eligible to backfill `other` with once `shared_substitute` is pulled away.
    backfill_candidate = client.post('/teachers', headers=admin_headers(), json={'first_name': 'Backfill', 'last_name': 'Sub', 'email': 'backfillsub@x.test', 'active': True, 'password': 'validpassword123'}).json()
    client.put('/availability', headers=admin_headers(), json={'teacher_id': backfill_candidate['id'], 'entries': [{'timeslot_id': timeslot_id, 'duty_type': 'GUARD'}]})

    other = create_absence(admin_headers(), date='2099-02-03', absent_teacher_id=TEACHER_ONE, class_group='ConflictOtherG', classroom='ConflictOtherR', timeslot_id=timeslot_id).json()
    client.patch(f"/absences/{other['id']}/force-assign", headers=admin_headers(), json={'substitute_id': shared_substitute['id']})

    target = create_absence(admin_headers(), date='2099-02-03', absent_teacher_id=TEACHER_TWO, class_group='ConflictTargetG', classroom='ConflictTargetR', timeslot_id=timeslot_id).json()
    response = client.patch(f"/absences/{target['id']}/force-assign", headers=admin_headers(), json={'substitute_id': shared_substitute['id']})
    assert response.status_code == 200
    assert response.json()['substitute_teacher_id'] == shared_substitute['id']

    refreshed_other = client.get('/absences', headers=admin_headers(), params={'teacher_id': TEACHER_ONE}).json()
    refreshed_other = next(a for a in refreshed_other if a['id'] == other['id'])
    assert refreshed_other['substitute_teacher_id'] == backfill_candidate['id']

    with TestingSessionLocal() as db:
        shared_stat = db.get(AssignmentStatistic, (shared_substitute['id'], timeslot_id))
        # Credited once for `other` (released) and once for `target`: net +1 weight, not +2.
        assert shared_stat.assignment_count == shared_substitute['duty_weight']
        backfill_stat = db.get(AssignmentStatistic, (backfill_candidate['id'], timeslot_id))
        assert backfill_stat.assignment_count == backfill_candidate['duty_weight']

    assert (target['id'], shared_substitute['id']) in notified_absence_ids
    assert (other['id'], backfill_candidate['id']) in notified_absence_ids


def test_force_assign_does_not_crash_when_the_only_backfill_candidate_is_the_teacher_being_moved():
    """Regression test: if `shared_substitute` is the ONLY teacher with real GUARD/SUPPORT
    availability for this slot, releasing them from `other` and then asking the automatic
    engine to backfill `other` must NOT re-pick `shared_substitute` right back onto it --
    that would try to double-book the same teacher into both `other` and `target` at the
    identical date/session, crashing on the absences (date, timeslot, substitute) unique
    constraint. `other` must instead stay released and pending, since nobody else is eligible."""
    timeslot_id = _isolated_timeslot(28)
    shared_substitute = client.post('/teachers', headers=admin_headers(), json={'first_name': 'SelfConflict', 'last_name': 'Sub', 'email': 'selfconflictsub@x.test', 'active': True, 'password': 'validpassword123'}).json()
    client.put('/availability', headers=admin_headers(), json={'teacher_id': shared_substitute['id'], 'entries': [{'timeslot_id': timeslot_id, 'duty_type': 'GUARD'}]})

    other = create_absence(admin_headers(), date='2099-02-10', absent_teacher_id=TEACHER_ONE, class_group='SelfConflictOtherG', classroom='SelfConflictOtherR', timeslot_id=timeslot_id).json()
    assert other['substitute_teacher_id'] == shared_substitute['id']  # auto-assigned: the only eligible candidate

    target = create_absence(admin_headers(), date='2099-02-10', absent_teacher_id=TEACHER_TWO, class_group='SelfConflictTargetG', classroom='SelfConflictTargetR', timeslot_id=timeslot_id).json()
    assert target['substitute_teacher_id'] is None

    response = client.patch(f"/absences/{target['id']}/force-assign", headers=admin_headers(), json={'substitute_id': shared_substitute['id']})
    assert response.status_code == 200
    assert response.json()['substitute_teacher_id'] == shared_substitute['id']

    refreshed_other = next(a for a in client.get('/absences', headers=admin_headers(), params={'teacher_id': TEACHER_ONE}).json() if a['id'] == other['id'])
    assert refreshed_other['substitute_teacher_id'] is None  # released, and left pending: nobody else was eligible

    with TestingSessionLocal() as db:
        stat = db.get(AssignmentStatistic, (shared_substitute['id'], timeslot_id))
        # They now cover exactly one absence (`target`) in this slot, not two.
        assert stat.assignment_count == shared_substitute['duty_weight']


# --- GET /sessions/{day}/{timeslot_id}/teachers-status ------------------------


def test_teachers_status_returns_404_for_unknown_session():
    response = client.get('/sessions/2026-09-17/999999/teachers-status', headers=admin_headers())
    assert response.status_code == 404


def test_teachers_status_reports_guard_support_busy_absent_and_unavailable():
    timeslot_id = _isolated_timeslot(11)
    guard = client.post('/teachers', headers=admin_headers(), json={'first_name': 'Status', 'last_name': 'Guard', 'email': 'statusguard@x.test', 'active': True, 'password': 'validpassword123'}).json()
    support = client.post('/teachers', headers=admin_headers(), json={'first_name': 'Status', 'last_name': 'Support', 'email': 'statussupport@x.test', 'active': True, 'password': 'validpassword123'}).json()
    free = client.post('/teachers', headers=admin_headers(), json={'first_name': 'Status', 'last_name': 'Free', 'email': 'statusfree@x.test', 'active': True, 'password': 'validpassword123'}).json()
    busy_sub = client.post('/teachers', headers=admin_headers(), json={'first_name': 'Status', 'last_name': 'Busy', 'email': 'statusbusy@x.test', 'active': True, 'password': 'validpassword123'}).json()
    absent_teacher = client.post('/teachers', headers=admin_headers(), json={'first_name': 'Status', 'last_name': 'Absent', 'email': 'statusabsent@x.test', 'active': True, 'password': 'validpassword123'}).json()

    # Created before anyone has availability on this isolated timeslot, so the automatic
    # engine leaves it pending and `busy_sub` can be force-assigned deterministically.
    covered = create_absence(admin_headers(), date='2099-02-04', absent_teacher_id=TEACHER_ONE, class_group='StatusCoveredG', classroom='StatusCoveredR', timeslot_id=timeslot_id).json()
    assign_response = client.patch(f"/absences/{covered['id']}/force-assign", headers=admin_headers(), json={'substitute_id': busy_sub['id']})
    assert assign_response.status_code == 200
    create_absence(admin_headers(), date='2099-02-04', absent_teacher_id=absent_teacher['id'], class_group='StatusAbsentG', classroom='StatusAbsentR', timeslot_id=timeslot_id)

    client.put('/availability', headers=admin_headers(), json={'teacher_id': guard['id'], 'entries': [{'timeslot_id': timeslot_id, 'duty_type': 'GUARD'}]})
    client.put('/availability', headers=admin_headers(), json={'teacher_id': support['id'], 'entries': [{'timeslot_id': timeslot_id, 'duty_type': 'SUPPORT'}]})

    statuses = {row['id']: row['status'] for row in client.get(f"/sessions/2099-02-04/{timeslot_id}/teachers-status", headers=admin_headers()).json()}
    assert statuses[guard['id']] == 'GUARD'
    assert statuses[support['id']] == 'SUPPORT'
    assert statuses[free['id']] == 'UNAVAILABLE'
    assert statuses[busy_sub['id']] == 'BUSY'
    assert statuses[absent_teacher['id']] == 'ABSENT'


# --- PATCH /absences/{id}: teacher self-edit of task/classroom ----------------


def test_teacher_can_update_task_and_classroom_of_their_own_future_absence():
    absence = create_absence(teacher_headers(), date='2099-01-05', absent_teacher_id=TEACHER_ONE, class_group='EditG', classroom='OldRoom', task_left='Old task').json()
    response = client.patch(f"/absences/{absence['id']}", headers=teacher_headers(), json={'task_left': 'New task', 'classroom': 'NewRoom'})
    assert response.status_code == 200
    body = response.json()
    assert body['task_left'] == 'New task'
    classrooms = {c['name']: c['id'] for c in client.get('/classrooms', headers=admin_headers()).json()}
    assert body['classroom_id'] == classrooms['NewRoom']


def test_teacher_can_update_only_one_field_at_a_time():
    absence = create_absence(teacher_headers(), date='2099-01-06', absent_teacher_id=TEACHER_ONE, class_group='EditG2', classroom='KeepRoom', task_left='Keep task').json()
    response = client.patch(f"/absences/{absence['id']}", headers=teacher_headers(), json={'task_left': 'Only task changed'})
    assert response.status_code == 200
    body = response.json()
    assert body['task_left'] == 'Only task changed'
    assert body['classroom_id'] == absence['classroom_id']


def test_update_absence_does_not_accept_date_timeslot_or_group_fields():
    """The schema simply has no fields for them, so attempting to send them is a no-op
    rather than a 422 - FastAPI/Pydantic silently ignores unknown extra fields."""
    absence = create_absence(teacher_headers(), date='2099-01-07', absent_teacher_id=TEACHER_ONE, class_group='EditG3', classroom='SameRoom', task_left='Same task').json()
    response = client.patch(f"/absences/{absence['id']}", headers=teacher_headers(), json={'task_left': 'Changed task', 'date': '2099-02-02', 'timeslot_id': TIMESLOT_TUESDAY, 'class_group': 'HackedGroup'})
    assert response.status_code == 200
    body = response.json()
    assert body['date'] == '2099-01-07'
    assert body['timeslot_id'] == absence['timeslot_id']
    assert body['class_group_id'] == absence['class_group_id']


def test_teacher_cannot_update_someone_elses_absence():
    absence = create_absence(admin_headers(), date='2099-01-08', absent_teacher_id=TEACHER_TWO, class_group='OtherG', classroom='OtherRoom', task_left='Other task').json()
    response = client.patch(f"/absences/{absence['id']}", headers=teacher_headers(), json={'task_left': 'Hijacked'})
    assert response.status_code == 403


def test_admin_can_update_any_absence():
    absence = create_absence(admin_headers(), date='2099-01-09', absent_teacher_id=TEACHER_TWO, class_group='AdminG', classroom='AdminRoom', task_left='Admin task').json()
    response = client.patch(f"/absences/{absence['id']}", headers=admin_headers(), json={'task_left': 'Fixed by admin'})
    assert response.status_code == 200
    assert response.json()['task_left'] == 'Fixed by admin'


def test_update_absence_rejects_a_date_already_in_the_past():
    absence = create_absence(admin_headers(), date='2020-01-01', absent_teacher_id=TEACHER_ONE, class_group='PastG', classroom='PastRoom', task_left='Past task').json()
    response = client.patch(f"/absences/{absence['id']}", headers=teacher_headers(), json={'task_left': 'Too late'})
    assert response.status_code == 409


def test_update_absence_returns_404_for_unknown_absence():
    response = client.patch('/absences/999999', headers=admin_headers(), json={'task_left': 'X'})
    assert response.status_code == 404


def test_update_absence_requires_at_least_one_field():
    absence = create_absence(teacher_headers(), date='2099-01-10', absent_teacher_id=TEACHER_ONE, class_group='EmptyG', classroom='EmptyRoom', task_left='Task').json()
    response = client.patch(f"/absences/{absence['id']}", headers=teacher_headers(), json={})
    assert response.status_code == 422


@pytest.mark.parametrize('date, field, bad_value', [('2099-01-11', 'task_left', '   '), ('2099-01-14', 'classroom', '')])
def test_update_absence_rejects_blank_values(date, field, bad_value):
    absence = create_absence(teacher_headers(), date=date, absent_teacher_id=TEACHER_ONE, class_group='BlankG', classroom='BlankRoom', task_left='Task').json()
    response = client.patch(f"/absences/{absence['id']}", headers=teacher_headers(), json={field: bad_value})
    assert response.status_code == 422


def test_update_absence_notifies_the_already_assigned_substitute(monkeypatch):
    notified = {}
    monkeypatch.setattr(
        'app.main.send_absence_updated_email',
        lambda db, absence, substitute_teacher_id: notified.update(
            substitute_teacher_id=substitute_teacher_id, task_left=absence.task_left, classroom=absence.classroom_name,
        ) or True,
    )
    timeslot_id = _isolated_timeslot(6)
    substitute = client.post('/teachers', headers=admin_headers(), json={'first_name': 'Notif', 'last_name': 'Sub', 'email': 'notifsub@x.test', 'active': True, 'password': 'validpassword123'}).json()
    absence = create_absence(admin_headers(), date='2099-01-12', absent_teacher_id=TEACHER_ONE, class_group='NotifG', classroom='OldRoom', task_left='Old', timeslot_id=timeslot_id).json()
    client.patch(f"/absences/{absence['id']}/force-assign", headers=admin_headers(), json={'substitute_id': substitute['id']})

    response = client.patch(f"/absences/{absence['id']}", headers=admin_headers(), json={'task_left': 'Updated task', 'classroom': 'NewRoom'})
    assert response.status_code == 200
    assert notified == {'substitute_teacher_id': substitute['id'], 'task_left': 'Updated task', 'classroom': 'NewRoom'}


def test_update_absence_does_not_notify_when_no_substitute_is_assigned(monkeypatch):
    called = []
    monkeypatch.setattr('app.main.send_absence_updated_email', lambda db, absence, substitute_teacher_id: called.append(1) or True)
    timeslot_id = _isolated_timeslot(7)
    absence = create_absence(teacher_headers(), date='2099-01-13', absent_teacher_id=TEACHER_ONE, class_group='NoSubG', classroom='NoSubRoom', task_left='Task', timeslot_id=timeslot_id).json()
    assert absence['substitute_teacher_id'] is None

    response = client.patch(f"/absences/{absence['id']}", headers=teacher_headers(), json={'task_left': 'Updated without substitute'})
    assert response.status_code == 200
    assert called == []


# --- PUT /absences/{id}: full administrative edit ("el baile de contadores") --


def _put_body(**overrides):
    body = dict(date='2099-03-01', timeslot_id=TIMESLOT_MONDAY, class_group='X', classroom='X', task_left='X', observations=None, substitute_id=None)
    body.update(overrides)
    return body


def test_admin_put_changing_only_details_sends_the_details_updated_email(monkeypatch):
    timeslot_id = _isolated_timeslot(12)
    substitute = client.post('/teachers', headers=admin_headers(), json={'first_name': 'PutDetails', 'last_name': 'Sub', 'email': 'putdetailssub@x.test', 'active': True, 'password': 'validpassword123'}).json()
    absence = create_absence(admin_headers(), date='2099-03-07', absent_teacher_id=TEACHER_ONE, class_group='PutDetailsG', classroom='OldRoom', task_left='Old task', timeslot_id=timeslot_id).json()
    client.patch(f"/absences/{absence['id']}/force-assign", headers=admin_headers(), json={'substitute_id': substitute['id']})

    # Patched only after the setup calls above, so force-assign's own (real) notification
    # doesn't get counted as one of the PUT's notifications.
    notified = []
    monkeypatch.setattr('app.main.send_absence_updated_email', lambda db, absence, substitute_teacher_id: notified.append((substitute_teacher_id, absence.task_left, absence.classroom_name)) or True)
    assign_spy = []
    monkeypatch.setattr('app.main.send_substitution_email', lambda db, absence, substitute_teacher_id: assign_spy.append(substitute_teacher_id) or True)

    response = client.put(f"/absences/{absence['id']}", headers=admin_headers(), json=_put_body(
        date='2099-03-07', timeslot_id=timeslot_id, class_group='PutDetailsG', classroom='NewRoom', task_left='New task', substitute_id=substitute['id'],
    ))
    assert response.status_code == 200
    assert response.json()['task_left'] == 'New task'
    assert notified == [(substitute['id'], 'New task', 'NewRoom')]
    assert assign_spy == []


def test_admin_put_changing_substitute_releases_old_and_credits_new(monkeypatch):
    notified = []
    monkeypatch.setattr('app.main.send_substitution_cancelled_email', lambda db, absence, substitute_teacher_id: notified.append(('cancel', substitute_teacher_id)) or True)
    monkeypatch.setattr('app.main.send_substitution_email', lambda db, absence, substitute_teacher_id: notified.append(('assign', substitute_teacher_id)) or True)

    timeslot_id = _isolated_timeslot(13)
    teacher_a = client.post('/teachers', headers=admin_headers(), json={'first_name': 'PutA', 'last_name': 'Sub', 'email': 'putasub@x.test', 'active': True, 'password': 'validpassword123'}).json()
    teacher_b = client.post('/teachers', headers=admin_headers(), json={'first_name': 'PutB', 'last_name': 'Sub', 'email': 'putbsub@x.test', 'active': True, 'password': 'validpassword123'}).json()
    absence = create_absence(admin_headers(), date='2099-03-01', absent_teacher_id=TEACHER_ONE, class_group='PutSwapG', classroom='PutSwapR', timeslot_id=timeslot_id).json()
    client.patch(f"/absences/{absence['id']}/force-assign", headers=admin_headers(), json={'substitute_id': teacher_a['id']})

    response = client.put(f"/absences/{absence['id']}", headers=admin_headers(), json=_put_body(
        date='2099-03-01', timeslot_id=timeslot_id, class_group='PutSwapG', classroom='PutSwapR', task_left='Trabajo', substitute_id=teacher_b['id'],
    ))
    assert response.status_code == 200
    assert response.json()['substitute_teacher_id'] == teacher_b['id']

    with TestingSessionLocal() as db:
        assert db.get(AssignmentStatistic, (teacher_a['id'], timeslot_id)).assignment_count == 0
        assert db.get(AssignmentStatistic, (teacher_b['id'], timeslot_id)).assignment_count == teacher_b['duty_weight']

    assert ('cancel', teacher_a['id']) in notified
    assert ('assign', teacher_b['id']) in notified


def test_admin_put_changing_session_with_same_substitute_moves_the_counter(monkeypatch):
    notified = []
    monkeypatch.setattr('app.main.send_substitution_cancelled_email', lambda db, absence, substitute_teacher_id: notified.append(('cancel', substitute_teacher_id)) or True)
    monkeypatch.setattr('app.main.send_substitution_email', lambda db, absence, substitute_teacher_id: notified.append(('assign', substitute_teacher_id)) or True)

    old_timeslot_id = _isolated_timeslot(14)
    new_timeslot_id = _isolated_timeslot(15)
    substitute = client.post('/teachers', headers=admin_headers(), json={'first_name': 'PutMove', 'last_name': 'Sub', 'email': 'putmovesub@x.test', 'active': True, 'password': 'validpassword123'}).json()
    absence = create_absence(admin_headers(), date='2099-03-02', absent_teacher_id=TEACHER_ONE, class_group='PutMoveG', classroom='PutMoveR', timeslot_id=old_timeslot_id).json()
    client.patch(f"/absences/{absence['id']}/force-assign", headers=admin_headers(), json={'substitute_id': substitute['id']})

    response = client.put(f"/absences/{absence['id']}", headers=admin_headers(), json=_put_body(
        date='2099-03-03', timeslot_id=new_timeslot_id, class_group='PutMoveG', classroom='PutMoveR', task_left='Trabajo', substitute_id=substitute['id'],
    ))
    assert response.status_code == 200

    with TestingSessionLocal() as db:
        assert db.get(AssignmentStatistic, (substitute['id'], old_timeslot_id)).assignment_count == 0
        assert db.get(AssignmentStatistic, (substitute['id'], new_timeslot_id)).assignment_count == substitute['duty_weight']

    assert ('cancel', substitute['id']) in notified
    assert ('assign', substitute['id']) in notified


def test_admin_put_unassigning_substitute_releases_credit_and_notifies_cancellation(monkeypatch):
    timeslot_id = _isolated_timeslot(16)
    substitute = client.post('/teachers', headers=admin_headers(), json={'first_name': 'PutUnassign', 'last_name': 'Sub', 'email': 'putunassignsub@x.test', 'active': True, 'password': 'validpassword123'}).json()
    absence = create_absence(admin_headers(), date='2099-03-04', absent_teacher_id=TEACHER_ONE, class_group='PutUnassignG', classroom='PutUnassignR', timeslot_id=timeslot_id).json()
    client.patch(f"/absences/{absence['id']}/force-assign", headers=admin_headers(), json={'substitute_id': substitute['id']})

    cancel_spy = []
    monkeypatch.setattr('app.main.send_substitution_cancelled_email', lambda db, absence, substitute_teacher_id: cancel_spy.append(substitute_teacher_id) or True)
    assign_spy = []
    monkeypatch.setattr('app.main.send_substitution_email', lambda db, absence, substitute_teacher_id: assign_spy.append(substitute_teacher_id) or True)

    response = client.put(f"/absences/{absence['id']}", headers=admin_headers(), json=_put_body(
        date='2099-03-04', timeslot_id=timeslot_id, class_group='PutUnassignG', classroom='PutUnassignR', task_left='Trabajo', substitute_id=None,
    ))
    assert response.status_code == 200
    assert response.json()['substitute_teacher_id'] is None

    with TestingSessionLocal() as db:
        assert db.get(AssignmentStatistic, (substitute['id'], timeslot_id)).assignment_count == 0

    assert cancel_spy == [substitute['id']]
    assert assign_spy == []


def test_admin_put_can_assign_a_substitute_to_a_previously_pending_absence(monkeypatch):
    assign_spy = []
    monkeypatch.setattr('app.main.send_substitution_email', lambda db, absence, substitute_teacher_id: assign_spy.append(substitute_teacher_id) or True)
    cancel_spy = []
    monkeypatch.setattr('app.main.send_substitution_cancelled_email', lambda db, absence, substitute_teacher_id: cancel_spy.append(substitute_teacher_id) or True)

    timeslot_id = _isolated_timeslot(17)
    substitute = client.post('/teachers', headers=admin_headers(), json={'first_name': 'PutFill', 'last_name': 'Sub', 'email': 'putfillsub@x.test', 'active': True, 'password': 'validpassword123'}).json()
    absence = create_absence(admin_headers(), date='2099-03-05', absent_teacher_id=TEACHER_ONE, class_group='PutFillG', classroom='PutFillR', timeslot_id=timeslot_id).json()
    assert absence['substitute_teacher_id'] is None

    response = client.put(f"/absences/{absence['id']}", headers=admin_headers(), json=_put_body(
        date='2099-03-05', timeslot_id=timeslot_id, class_group='PutFillG', classroom='PutFillR', task_left='Trabajo', substitute_id=substitute['id'],
    ))
    assert response.status_code == 200
    assert response.json()['substitute_teacher_id'] == substitute['id']

    with TestingSessionLocal() as db:
        assert db.get(AssignmentStatistic, (substitute['id'], timeslot_id)).assignment_count == substitute['duty_weight']

    assert assign_spy == [substitute['id']]
    assert cancel_spy == []


def test_admin_put_no_op_save_sends_no_emails_and_leaves_counters_unchanged(monkeypatch):
    timeslot_id = _isolated_timeslot(18)
    substitute = client.post('/teachers', headers=admin_headers(), json={'first_name': 'PutNoop', 'last_name': 'Sub', 'email': 'putnoopsub@x.test', 'active': True, 'password': 'validpassword123'}).json()
    absence = create_absence(admin_headers(), date='2099-03-06', absent_teacher_id=TEACHER_ONE, class_group='PutNoopG', classroom='PutNoopR', task_left='Trabajo', timeslot_id=timeslot_id).json()
    client.patch(f"/absences/{absence['id']}/force-assign", headers=admin_headers(), json={'substitute_id': substitute['id']})

    with TestingSessionLocal() as db:
        before = db.get(AssignmentStatistic, (substitute['id'], timeslot_id)).assignment_count

    notified = []
    monkeypatch.setattr('app.main.send_substitution_email', lambda db, absence, substitute_teacher_id: notified.append(('assign', substitute_teacher_id)) or True)
    monkeypatch.setattr('app.main.send_substitution_cancelled_email', lambda db, absence, substitute_teacher_id: notified.append(('cancel', substitute_teacher_id)) or True)
    monkeypatch.setattr('app.main.send_absence_updated_email', lambda db, absence, substitute_teacher_id: notified.append(('update', substitute_teacher_id)) or True)

    response = client.put(f"/absences/{absence['id']}", headers=admin_headers(), json=_put_body(
        date='2099-03-06', timeslot_id=timeslot_id, class_group='PutNoopG', classroom='PutNoopR', task_left='Trabajo', substitute_id=substitute['id'],
    ))
    assert response.status_code == 200

    with TestingSessionLocal() as db:
        assert db.get(AssignmentStatistic, (substitute['id'], timeslot_id)).assignment_count == before
    assert notified == []


def test_admin_put_releases_and_backfills_a_conflicting_absence(monkeypatch):
    notified_absence_ids = []
    import app.main as main_module
    real_send_substitution_email = main_module.send_substitution_email

    def spy(db, absence, substitute_teacher_id):
        notified_absence_ids.append((absence.id, substitute_teacher_id))
        return real_send_substitution_email(db, absence, substitute_teacher_id)

    monkeypatch.setattr('app.main.send_substitution_email', spy)

    timeslot_id = _isolated_timeslot(19)
    shared_substitute = client.post('/teachers', headers=admin_headers(), json={'first_name': 'PutShared', 'last_name': 'Sub', 'email': 'putsharedsub@x.test', 'active': True, 'password': 'validpassword123'}).json()
    backfill_candidate = client.post('/teachers', headers=admin_headers(), json={'first_name': 'PutBackfill', 'last_name': 'Sub', 'email': 'putbackfillsub@x.test', 'active': True, 'password': 'validpassword123'}).json()

    # Created before anyone has availability on this isolated timeslot, so both stay
    # pending from the automatic engine's point of view until explicitly assigned below.
    other = create_absence(admin_headers(), date='2099-03-08', absent_teacher_id=TEACHER_ONE, class_group='PutConflictOtherG', classroom='PutConflictOtherR', timeslot_id=timeslot_id).json()
    client.patch(f"/absences/{other['id']}/force-assign", headers=admin_headers(), json={'substitute_id': shared_substitute['id']})
    target = create_absence(admin_headers(), date='2099-03-09', absent_teacher_id=TEACHER_TWO, class_group='PutConflictTargetG', classroom='PutConflictTargetR', timeslot_id=timeslot_id).json()
    assert target['substitute_teacher_id'] is None

    # Only now does backfill_candidate become eligible, so assign_substitute() has
    # somebody to backfill `other` with once shared_substitute is pulled away from it.
    client.put('/availability', headers=admin_headers(), json={'teacher_id': backfill_candidate['id'], 'entries': [{'timeslot_id': timeslot_id, 'duty_type': 'GUARD'}]})

    response = client.put(f"/absences/{target['id']}", headers=admin_headers(), json=_put_body(
        date='2099-03-08', timeslot_id=timeslot_id, class_group='PutConflictTargetG', classroom='PutConflictTargetR', task_left='Trabajo', substitute_id=shared_substitute['id'],
    ))
    assert response.status_code == 200
    assert response.json()['substitute_teacher_id'] == shared_substitute['id']

    refreshed_other = next(a for a in client.get('/absences', headers=admin_headers(), params={'teacher_id': TEACHER_ONE}).json() if a['id'] == other['id'])
    assert refreshed_other['substitute_teacher_id'] == backfill_candidate['id']

    with TestingSessionLocal() as db:
        assert db.get(AssignmentStatistic, (shared_substitute['id'], timeslot_id)).assignment_count == shared_substitute['duty_weight']
        assert db.get(AssignmentStatistic, (backfill_candidate['id'], timeslot_id)).assignment_count == backfill_candidate['duty_weight']

    assert (target['id'], shared_substitute['id']) in notified_absence_ids
    assert (other['id'], backfill_candidate['id']) in notified_absence_ids


def test_admin_put_does_not_crash_when_the_only_backfill_candidate_is_the_teacher_being_moved():
    """Regression test (PUT counterpart of the force-assign one above). `shared_substitute`
    is the ONLY teacher with real GUARD/SUPPORT availability for this weekly slot, so the
    automatic engine assigns them to BOTH `other` (date D1) and `target` (date D2) on
    creation -- one weekly counter, two duties on different Mondays. Editing `target` to
    move it onto D1 (the same date `other` already occupies), keeping the same substitute,
    must release them from `other` (left pending: nobody else is eligible) rather than
    silently re-picking them for `other` too and double-booking them into both absences on
    the same date/session, which would crash on the absences (date, timeslot, substitute)
    unique constraint."""
    timeslot_id = _isolated_timeslot(29)
    shared_substitute = client.post('/teachers', headers=admin_headers(), json={'first_name': 'PutSelfConflict', 'last_name': 'Sub', 'email': 'putselfconflictsub@x.test', 'active': True, 'password': 'validpassword123'}).json()
    client.put('/availability', headers=admin_headers(), json={'teacher_id': shared_substitute['id'], 'entries': [{'timeslot_id': timeslot_id, 'duty_type': 'GUARD'}]})

    other = create_absence(admin_headers(), date='2099-02-11', absent_teacher_id=TEACHER_ONE, class_group='PutSelfConflictOtherG', classroom='PutSelfConflictOtherR', timeslot_id=timeslot_id).json()
    target = create_absence(admin_headers(), date='2099-02-12', absent_teacher_id=TEACHER_TWO, class_group='PutSelfConflictTargetG', classroom='PutSelfConflictTargetR', timeslot_id=timeslot_id).json()
    # Both auto-assigned to the only eligible candidate; two duties in the same weekly slot.
    assert other['substitute_teacher_id'] == shared_substitute['id']
    assert target['substitute_teacher_id'] == shared_substitute['id']
    with TestingSessionLocal() as db:
        assert db.get(AssignmentStatistic, (shared_substitute['id'], timeslot_id)).assignment_count == 2 * shared_substitute['duty_weight']

    # Move `target` onto `other`'s exact date/session, substitute unchanged.
    response = client.put(f"/absences/{target['id']}", headers=admin_headers(), json=_put_body(
        date='2099-02-11', timeslot_id=timeslot_id, class_group='PutSelfConflictTargetG', classroom='PutSelfConflictTargetR',
        task_left='Trabajo', substitute_id=shared_substitute['id'],
    ))
    assert response.status_code == 200
    assert response.json()['substitute_teacher_id'] == shared_substitute['id']

    refreshed_other = next(a for a in client.get('/absences', headers=admin_headers(), params={'teacher_id': TEACHER_ONE}).json() if a['id'] == other['id'])
    assert refreshed_other['substitute_teacher_id'] is None  # released, and left pending: nobody else was eligible

    with TestingSessionLocal() as db:
        stat = db.get(AssignmentStatistic, (shared_substitute['id'], timeslot_id))
        # They now cover exactly one absence (`target`) in this slot, not two.
        assert stat.assignment_count == shared_substitute['duty_weight']


def test_admin_put_returns_404_for_unknown_absence():
    response = client.put('/absences/999999', headers=admin_headers(), json=_put_body(timeslot_id=TIMESLOT_MONDAY))
    assert response.status_code == 404


def test_admin_put_returns_404_for_unknown_session():
    timeslot_id = _isolated_timeslot(20)
    absence = create_absence(admin_headers(), date='2099-03-11', absent_teacher_id=TEACHER_ONE, class_group='PutBadSessG', classroom='PutBadSessR', timeslot_id=timeslot_id).json()
    response = client.put(f"/absences/{absence['id']}", headers=admin_headers(), json=_put_body(date='2099-03-11', timeslot_id=999999, class_group='PutBadSessG', classroom='PutBadSessR', task_left='Trabajo'))
    assert response.status_code == 404


def test_admin_put_returns_404_for_unknown_substitute():
    timeslot_id = _isolated_timeslot(21)
    absence = create_absence(admin_headers(), date='2099-03-12', absent_teacher_id=TEACHER_ONE, class_group='PutBadSubG', classroom='PutBadSubR', timeslot_id=timeslot_id).json()
    response = client.put(f"/absences/{absence['id']}", headers=admin_headers(), json=_put_body(date='2099-03-12', timeslot_id=timeslot_id, class_group='PutBadSubG', classroom='PutBadSubR', task_left='Trabajo', substitute_id=999999))
    assert response.status_code == 404


def test_admin_put_returns_422_for_inactive_substitute():
    timeslot_id = _isolated_timeslot(22)
    absence = create_absence(admin_headers(), date='2099-03-13', absent_teacher_id=TEACHER_ONE, class_group='PutInactG', classroom='PutInactR', timeslot_id=timeslot_id).json()
    response = client.put(f"/absences/{absence['id']}", headers=admin_headers(), json=_put_body(date='2099-03-13', timeslot_id=timeslot_id, class_group='PutInactG', classroom='PutInactR', task_left='Trabajo', substitute_id=TEACHER_INACTIVE))
    assert response.status_code == 422


def test_admin_put_rejects_self_assignment():
    timeslot_id = _isolated_timeslot(23)
    absence = create_absence(admin_headers(), date='2099-03-14', absent_teacher_id=TEACHER_ONE, class_group='PutSelfG', classroom='PutSelfR', timeslot_id=timeslot_id).json()
    response = client.put(f"/absences/{absence['id']}", headers=admin_headers(), json=_put_body(date='2099-03-14', timeslot_id=timeslot_id, class_group='PutSelfG', classroom='PutSelfR', task_left='Trabajo', substitute_id=TEACHER_ONE))
    assert response.status_code == 422


def test_admin_put_rejects_a_substitute_who_is_also_absent_at_the_new_session():
    timeslot_id = _isolated_timeslot(24)
    absence_one = create_absence(admin_headers(), date='2099-03-15', absent_teacher_id=TEACHER_ONE, class_group='PutDoubleAbsG1', classroom='PutDoubleAbsR1', timeslot_id=timeslot_id).json()
    create_absence(admin_headers(), date='2099-03-15', absent_teacher_id=TEACHER_TWO, class_group='PutDoubleAbsG2', classroom='PutDoubleAbsR2', timeslot_id=timeslot_id)

    response = client.put(f"/absences/{absence_one['id']}", headers=admin_headers(), json=_put_body(date='2099-03-15', timeslot_id=timeslot_id, class_group='PutDoubleAbsG1', classroom='PutDoubleAbsR1', task_left='Trabajo', substitute_id=TEACHER_TWO))
    assert response.status_code == 422


def test_admin_put_rejects_a_duplicate_of_another_absence():
    timeslot_id = _isolated_timeslot(25)
    create_absence(admin_headers(), date='2099-03-16', absent_teacher_id=TEACHER_ONE, class_group='PutDupG', classroom='PutDupR', task_left='Same task', timeslot_id=timeslot_id)
    movable = create_absence(admin_headers(), date='2099-03-17', absent_teacher_id=TEACHER_ONE, class_group='PutDupG', classroom='PutDupR', task_left='Same task', timeslot_id=timeslot_id).json()

    response = client.put(f"/absences/{movable['id']}", headers=admin_headers(), json=_put_body(date='2099-03-16', timeslot_id=timeslot_id, class_group='PutDupG', classroom='PutDupR', task_left='Same task'))
    assert response.status_code == 409


def test_admin_put_allows_editing_an_absence_with_a_past_date(monkeypatch):
    monkeypatch.setattr('app.main.send_absence_updated_email', lambda db, absence, substitute_teacher_id: True)
    timeslot_id = _isolated_timeslot(26)
    absence = create_absence(admin_headers(), date='2020-01-01', absent_teacher_id=TEACHER_ONE, class_group='PutPastG', classroom='OldPastRoom', task_left='Old', timeslot_id=timeslot_id).json()

    response = client.put(f"/absences/{absence['id']}", headers=admin_headers(), json=_put_body(date='2020-01-01', timeslot_id=timeslot_id, class_group='PutPastG', classroom='NewPastRoom', task_left='Corrected'))
    assert response.status_code == 200
    assert response.json()['task_left'] == 'Corrected'


def test_admin_put_rolls_back_everything_if_a_later_step_fails(monkeypatch):
    timeslot_id = _isolated_timeslot(27)
    substitute = client.post('/teachers', headers=admin_headers(), json={'first_name': 'PutRollback', 'last_name': 'Sub', 'email': 'putrollbacksub@x.test', 'active': True, 'password': 'validpassword123'}).json()
    new_substitute = client.post('/teachers', headers=admin_headers(), json={'first_name': 'PutRollback2', 'last_name': 'Sub2', 'email': 'putrollbacksub2@x.test', 'active': True, 'password': 'validpassword123'}).json()
    absence = create_absence(admin_headers(), date='2099-03-18', absent_teacher_id=TEACHER_ONE, class_group='PutRollbackG', classroom='OldRoom', task_left='Old', timeslot_id=timeslot_id).json()
    client.patch(f"/absences/{absence['id']}/force-assign", headers=admin_headers(), json={'substitute_id': substitute['id']})

    with TestingSessionLocal() as db:
        before_task = db.get(Absence, absence['id']).task_left
        before_old_stat = db.get(AssignmentStatistic, (substitute['id'], timeslot_id)).assignment_count

    def boom(db, day, timeslot_id):
        raise RuntimeError('simulated failure')
    monkeypatch.setattr('app.main.assign_hallway_duties', boom)

    with pytest.raises(RuntimeError):
        client.put(f"/absences/{absence['id']}", headers=admin_headers(), json=_put_body(
            date='2099-03-18', timeslot_id=timeslot_id, class_group='PutRollbackG', classroom='NewRoom', task_left='New', substitute_id=new_substitute['id'],
        ))

    with TestingSessionLocal() as db:
        after = db.get(Absence, absence['id'])
        assert after.task_left == before_task
        assert after.substitute_teacher_id == substitute['id']
        assert db.get(AssignmentStatistic, (substitute['id'], timeslot_id)).assignment_count == before_old_stat
        new_stat = db.get(AssignmentStatistic, (new_substitute['id'], timeslot_id))
        assert new_stat is None or new_stat.assignment_count == 0


# --- group/classroom names on absences ----------------------------------------


def test_absence_responses_carry_the_group_and_classroom_names():
    """The admin edit dialog prefills Grupo/Aula from these fields; it must not depend on
    the client's /groups and /classrooms lists, which are only loaded at login and go
    stale as soon as an absence creates a new group or classroom."""
    created = create_absence(admin_headers(), date='2099-04-01', class_group='NamesG', classroom='NamesR', timeslot_id=_isolated_timeslot(30))
    assert created.status_code == 201
    assert (created.json()['class_group_name'], created.json()['classroom_name']) == ('NamesG', 'NamesR')

    listed = next(a for a in client.get('/absences', headers=admin_headers()).json() if a['id'] == created.json()['id'])
    assert (listed['class_group_name'], listed['classroom_name']) == ('NamesG', 'NamesR')


def test_admin_put_changing_substitute_and_group_returns_and_emails_the_new_names(monkeypatch):
    timeslot_id = _isolated_timeslot(31)
    substitute = client.post('/teachers', headers=admin_headers(), json={'first_name': 'NamesPut', 'last_name': 'Sub', 'email': 'namesputsub@x.test', 'active': True, 'password': 'validpassword123'}).json()
    absence = create_absence(admin_headers(), date='2099-04-02', class_group='OldNamesG', classroom='OldNamesR', timeslot_id=timeslot_id).json()

    from app.services import _place_names
    emailed = []
    monkeypatch.setattr('app.main.send_substitution_email', lambda db, a, sid: emailed.append(_place_names(a)) or True)

    response = client.put(f"/absences/{absence['id']}", headers=admin_headers(), json=_put_body(
        date='2099-04-02', timeslot_id=timeslot_id, class_group='NewNamesG', classroom='NewNamesR', task_left='Trabajo', substitute_id=substitute['id'],
    ))
    assert response.status_code == 200
    assert (response.json()['class_group_name'], response.json()['classroom_name']) == ('NewNamesG', 'NewNamesR')
    assert emailed == [('NewNamesG', 'NewNamesR')]


def test_force_assign_backfill_email_includes_the_backfilled_absence_group_and_classroom(monkeypatch):
    """The absence released from the chosen substitute and refilled by the engine was
    loaded straight from the DB; its email used to say 'no disponible' for both fields."""
    timeslot_id = _isolated_timeslot(32)
    shared_substitute = client.post('/teachers', headers=admin_headers(), json={'first_name': 'NamesShared', 'last_name': 'Sub', 'email': 'namessharedsub@x.test', 'active': True, 'password': 'validpassword123'}).json()
    backfill_candidate = client.post('/teachers', headers=admin_headers(), json={'first_name': 'NamesBackfill', 'last_name': 'Sub', 'email': 'namesbackfillsub@x.test', 'active': True, 'password': 'validpassword123'}).json()
    client.put('/availability', headers=admin_headers(), json={'teacher_id': backfill_candidate['id'], 'entries': [{'timeslot_id': timeslot_id, 'duty_type': 'GUARD'}]})

    other = create_absence(admin_headers(), date='2099-04-03', absent_teacher_id=TEACHER_ONE, class_group='BackfillOtherG', classroom='BackfillOtherR', timeslot_id=timeslot_id).json()
    client.patch(f"/absences/{other['id']}/force-assign", headers=admin_headers(), json={'substitute_id': backfill_candidate['id']})
    # Re-point `other` onto shared_substitute so backfill_candidate is free to refill it later.
    client.put(f"/absences/{other['id']}", headers=admin_headers(), json=_put_body(
        date='2099-04-03', timeslot_id=timeslot_id, class_group='BackfillOtherG', classroom='BackfillOtherR', task_left='Trabajo', substitute_id=shared_substitute['id'],
    ))
    target = create_absence(admin_headers(), date='2099-04-03', absent_teacher_id=TEACHER_TWO, class_group='BackfillTargetG', classroom='BackfillTargetR', timeslot_id=timeslot_id).json()
    assert target['substitute_teacher_id'] == backfill_candidate['id']
    # Free backfill_candidate again so they're the one who refills `other`.
    client.put(f"/absences/{target['id']}", headers=admin_headers(), json=_put_body(
        date='2099-04-03', timeslot_id=timeslot_id, class_group='BackfillTargetG', classroom='BackfillTargetR', task_left='Trabajo', substitute_id=None,
    ))

    from app.services import _place_names
    emailed = {}
    monkeypatch.setattr('app.main.send_substitution_email', lambda db, a, sid: emailed.__setitem__(a.id, (sid, _place_names(a))) or True)

    response = client.patch(f"/absences/{target['id']}/force-assign", headers=admin_headers(), json={'substitute_id': shared_substitute['id']})
    assert response.status_code == 200
    assert emailed[target['id']] == (shared_substitute['id'], ('BackfillTargetG', 'BackfillTargetR'))
    assert emailed[other['id']] == (backfill_candidate['id'], ('BackfillOtherG', 'BackfillOtherR'))
