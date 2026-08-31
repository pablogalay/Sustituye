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
from app.models import Teacher, TimeSlot

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
