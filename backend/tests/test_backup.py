"""Tests for the admin-only database backup endpoints (app.backup): export
produces a restorable JSON snapshot, import enforces admin/auth, rejects
malformed payloads, and round-trips data (including foreign keys) correctly.

Uses its own isolated in-memory SQLite database, same pattern as
test_main_endpoints.py, so it doesn't depend on state from other test
modules."""
import os
os.environ.setdefault('DATABASE_URL', 'sqlite:///./test_backup_unused.db')
os.environ.setdefault('JWT_SECRET', 'test-secret')

from datetime import date, time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import hash_password
from app.database import Base, get_db
from app.main import app
from app.models import Absence, AssignmentStatistic, Availability, ClassGroup, Classroom, Teacher, TimeSlot

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
    app.dependency_overrides[get_db] = _override_get_db
    yield
    del app.dependency_overrides[get_db]


with TestingSessionLocal() as _db:
    _db.add_all([
        TimeSlot(weekday='Monday', period_number=1, start_time=time(8, 25), end_time=time(9, 20)),
        Teacher(first_name='Teacher', last_name='One', email='teacher.one@x.test', active=True, password_hash=hash_password('password123')),
        Teacher(first_name='Teacher', last_name='Two', email='teacher.two@x.test', active=True, password_hash=hash_password('password123')),
        ClassGroup(name='3ESOA'),
        Classroom(name='B-204'),
    ])
    _db.commit()
    _db.add_all([
        Availability(teacher_id=1, timeslot_id=1, duty_type='GUARD'),
        AssignmentStatistic(teacher_id=1, timeslot_id=1, assignment_count=3),
        Absence(date=date(2026, 8, 10), timeslot_id=1, absent_teacher_id=1, class_group_id=1, classroom_id=1, task_left='Trabajo', substitute_teacher_id=2),
    ])
    _db.commit()


def admin_headers():
    token = client.post('/auth/login', json={'email': 'admin@school.local', 'password': 'admin123'}).json()['access_token']
    return {'Authorization': f'Bearer {token}'}


def teacher_headers():
    token = client.post('/auth/login', json={'email': 'teacher.one@x.test', 'password': 'password123'}).json()['access_token']
    return {'Authorization': f'Bearer {token}'}


def test_export_requires_admin():
    assert client.get('/backup/export', headers=teacher_headers()).status_code == 403
    assert client.get('/backup/export').status_code == 401


def test_import_requires_admin():
    files = {'file': ('backup.json', b'{"tables": {}}', 'application/json')}
    assert client.post('/backup/import', headers=teacher_headers(), files=files).status_code == 403


def test_export_returns_structured_snapshot_with_all_tables():
    response = client.get('/backup/export', headers=admin_headers())
    assert response.status_code == 200
    assert 'attachment' in response.headers['content-disposition']
    payload = response.json()
    assert set(payload['tables'].keys()) == {'class_groups', 'classrooms', 'teachers', 'timeslots', 'availability', 'assignment_statistics', 'absences'}
    assert len(payload['tables']['teachers']) == 2
    assert payload['tables']['absences'][0]['task_left'] == 'Trabajo'


def test_import_rejects_invalid_json():
    files = {'file': ('backup.json', b'not json', 'application/json')}
    response = client.post('/backup/import', headers=admin_headers(), files=files)
    assert response.status_code == 422


def test_import_rejects_missing_tables_key():
    files = {'file': ('backup.json', b'{"version": 1}', 'application/json')}
    response = client.post('/backup/import', headers=admin_headers(), files=files)
    assert response.status_code == 422


def test_export_then_import_round_trips_data_respecting_foreign_keys():
    exported = client.get('/backup/export', headers=admin_headers()).json()

    # Wipe everything, then restore from the exported snapshot.
    files = {'file': ('backup.json', __import__('json').dumps(exported).encode(), 'application/json')}
    response = client.post('/backup/import', headers=admin_headers(), files=files)
    assert response.status_code == 200
    body = response.json()
    assert body['ok'] is True
    assert body['tables_restored']['teachers'] == 2
    assert body['tables_restored']['absences'] == 1

    reexported = client.get('/backup/export', headers=admin_headers()).json()
    assert reexported['tables']['teachers'] == exported['tables']['teachers']
    assert reexported['tables']['absences'] == exported['tables']['absences']

    # The restored teacher/timeslot ids must still satisfy new FK inserts.
    absence_response = client.post('/absences', headers=admin_headers(), json={
        'date': '2026-08-11', 'timeslot_id': 1, 'absent_teacher_id': 2,
        'class_group': '3ESOA', 'classroom': 'B-204', 'task_left': 'Otra tarea', 'observations': None,
    })
    assert absence_response.status_code == 201
