"""Unit tests for app.services: assign_substitute() edge cases and every
branch of send_substitution_email() (SMTP configuration, missing records,
TLS/SSL variants, and infrastructure failures)."""
import os
os.environ.setdefault('DATABASE_URL', 'sqlite:///./test_services.db')
os.environ.setdefault('JWT_SECRET', 'test-secret')

import smtplib
from datetime import date, time
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Absence, Availability, Teacher, TimeSlot
from app.services import assign_substitute, send_substitution_email


# --- assign_substitute: SUPPORT fallback -------------------------------------


@pytest.fixture()
def db():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_assign_substitute_falls_back_to_support_when_no_guard_available(db):
    slot = TimeSlot(weekday='Monday', period_number=1, start_time=time(8), end_time=time(9))
    absent = Teacher(first_name='Absent', last_name='One', email='absent@x.test')
    support = Teacher(first_name='Support', last_name='One', email='support@x.test')
    db.add_all([slot, absent, support])
    db.flush()
    db.add(Availability(teacher_id=support.id, timeslot_id=slot.id, duty_type='SUPPORT'))
    db.commit()

    absence = Absence(date=date.today(), timeslot_id=slot.id, absent_teacher_id=absent.id, class_group_id=1, classroom_id=1, task_left='x')
    db.add(absence)
    db.flush()

    assert assign_substitute(db, absence) == support.id


def test_assign_substitute_prefers_guard_over_support(db):
    slot = TimeSlot(weekday='Monday', period_number=1, start_time=time(8), end_time=time(9))
    absent = Teacher(first_name='Absent', last_name='One', email='absent2@x.test')
    guard = Teacher(first_name='Guard', last_name='One', email='guard@x.test')
    support = Teacher(first_name='Support', last_name='One', email='support2@x.test')
    db.add_all([slot, absent, guard, support])
    db.flush()
    db.add_all([
        Availability(teacher_id=guard.id, timeslot_id=slot.id, duty_type='GUARD'),
        Availability(teacher_id=support.id, timeslot_id=slot.id, duty_type='SUPPORT'),
    ])
    db.commit()

    absence = Absence(date=date.today(), timeslot_id=slot.id, absent_teacher_id=absent.id, class_group_id=1, classroom_id=1, task_left='x')
    db.add(absence)
    db.flush()

    assert assign_substitute(db, absence) == guard.id


# --- send_substitution_email --------------------------------------------------


class FakeDB:
    def __init__(self, records):
        self._records = records

    def get(self, model, identifier):
        return self._records.get((model, identifier))


def _absence(**overrides):
    defaults = dict(
        id=1, date=date(2026, 8, 10), timeslot_id=1, absent_teacher_id=2,
        task_left='Trabajo', observations='-', class_group='4B', classroom='Aula 12',
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _clear_smtp_env(monkeypatch):
    for name in ['SMTP_HOST', 'SMTP_PORT', 'SMTP_USERNAME', 'SMTP_PASSWORD', 'SMTP_FROM', 'SMTP_USE_TLS', 'SMTP_USE_SSL']:
        monkeypatch.delenv(name, raising=False)


def test_returns_false_when_smtp_host_not_configured(monkeypatch):
    # sender always falls back to a non-empty default ('no-reply@localhost' or
    # SMTP_USERNAME), so an empty SMTP_HOST is what actually gates this branch.
    _clear_smtp_env(monkeypatch)
    assert send_substitution_email(FakeDB({}), _absence(), 1) is False


def test_returns_false_when_substitute_teacher_missing(monkeypatch):
    monkeypatch.setenv('SMTP_HOST', 'smtp.example.com')
    monkeypatch.setenv('SMTP_FROM', 'no-reply@example.com')
    time_slot = SimpleNamespace(weekday='Monday', period_number=1, start_time=time(9), end_time=time(10))
    db = FakeDB({(TimeSlot, 1): time_slot})
    assert send_substitution_email(db, _absence(timeslot_id=1), substitute_teacher_id=999) is False


def test_returns_false_when_timeslot_missing(monkeypatch):
    monkeypatch.setenv('SMTP_HOST', 'smtp.example.com')
    monkeypatch.setenv('SMTP_FROM', 'no-reply@example.com')
    substitute = SimpleNamespace(id=1, first_name='Ana', last_name='López', email='ana@example.com')
    db = FakeDB({(Teacher, 1): substitute})
    assert send_substitution_email(db, _absence(timeslot_id=999), substitute_teacher_id=1) is False


def _fake_db_with_recipients():
    substitute = SimpleNamespace(id=1, first_name='Ana', last_name='López', email='ana@example.com')
    absent = SimpleNamespace(first_name='Luis', last_name='Pérez')
    time_slot = SimpleNamespace(weekday='Monday', period_number=2, start_time=time(9, 0), end_time=time(10, 0))
    return FakeDB({(Teacher, 1): substitute, (Teacher, 2): absent, (TimeSlot, 1): time_slot})


class RecordingSMTP:
    calls = []

    def __init__(self, host, port, timeout=10):
        self.host = host
        self.port = port
        self.timeout = timeout
        RecordingSMTP.calls.append(('SMTP', host, port))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def starttls(self):
        RecordingSMTP.calls.append(('starttls',))

    def login(self, username, password):
        RecordingSMTP.calls.append(('login', username, password))

    def send_message(self, message):
        RecordingSMTP.calls.append(('send_message', message))


class RecordingSMTP_SSL(RecordingSMTP):
    def __init__(self, host, port, timeout=10):
        super().__init__(host, port, timeout)
        RecordingSMTP.calls[-1] = ('SMTP_SSL', host, port)


@pytest.fixture(autouse=True)
def _reset_recording_calls():
    RecordingSMTP.calls = []
    yield


def test_uses_ssl_transport_when_use_ssl_enabled(monkeypatch):
    monkeypatch.setenv('SMTP_HOST', 'smtp.example.com')
    monkeypatch.setenv('SMTP_FROM', 'no-reply@example.com')
    monkeypatch.setenv('SMTP_USE_SSL', 'true')
    monkeypatch.setattr('app.services.smtplib.SMTP', RecordingSMTP)
    monkeypatch.setattr('app.services.smtplib.SMTP_SSL', RecordingSMTP_SSL)

    assert send_substitution_email(_fake_db_with_recipients(), _absence(), 1) is True
    assert RecordingSMTP.calls[0][0] == 'SMTP_SSL'


def test_uses_ssl_transport_and_logs_in_when_both_configured(monkeypatch):
    monkeypatch.setenv('SMTP_HOST', 'smtp.example.com')
    monkeypatch.setenv('SMTP_FROM', 'no-reply@example.com')
    monkeypatch.setenv('SMTP_USE_SSL', 'true')
    monkeypatch.setenv('SMTP_USERNAME', 'user@example.com')
    monkeypatch.setenv('SMTP_PASSWORD', 'secret')
    monkeypatch.setattr('app.services.smtplib.SMTP', RecordingSMTP)
    monkeypatch.setattr('app.services.smtplib.SMTP_SSL', RecordingSMTP_SSL)

    assert send_substitution_email(_fake_db_with_recipients(), _absence(), 1) is True
    assert RecordingSMTP.calls[0][0] == 'SMTP_SSL'
    assert ('login', 'user@example.com', 'secret') in RecordingSMTP.calls


def test_skips_starttls_when_tls_disabled(monkeypatch):
    monkeypatch.setenv('SMTP_HOST', 'smtp.example.com')
    monkeypatch.setenv('SMTP_FROM', 'no-reply@example.com')
    monkeypatch.setenv('SMTP_USE_TLS', 'false')
    monkeypatch.setattr('app.services.smtplib.SMTP', RecordingSMTP)
    monkeypatch.setattr('app.services.smtplib.SMTP_SSL', RecordingSMTP_SSL)

    assert send_substitution_email(_fake_db_with_recipients(), _absence(), 1) is True
    assert ('starttls',) not in RecordingSMTP.calls


def test_calls_starttls_when_tls_enabled_by_default(monkeypatch):
    monkeypatch.setenv('SMTP_HOST', 'smtp.example.com')
    monkeypatch.setenv('SMTP_FROM', 'no-reply@example.com')
    monkeypatch.setattr('app.services.smtplib.SMTP', RecordingSMTP)
    monkeypatch.setattr('app.services.smtplib.SMTP_SSL', RecordingSMTP_SSL)

    assert send_substitution_email(_fake_db_with_recipients(), _absence(), 1) is True
    assert ('starttls',) in RecordingSMTP.calls


def test_skips_login_when_no_credentials_configured(monkeypatch):
    monkeypatch.setenv('SMTP_HOST', 'smtp.example.com')
    monkeypatch.setenv('SMTP_FROM', 'no-reply@example.com')
    monkeypatch.setattr('app.services.smtplib.SMTP', RecordingSMTP)
    monkeypatch.setattr('app.services.smtplib.SMTP_SSL', RecordingSMTP_SSL)

    assert send_substitution_email(_fake_db_with_recipients(), _absence(), 1) is True
    assert not any(call[0] == 'login' for call in RecordingSMTP.calls)


def test_logs_in_when_credentials_configured(monkeypatch):
    monkeypatch.setenv('SMTP_HOST', 'smtp.example.com')
    monkeypatch.setenv('SMTP_FROM', 'no-reply@example.com')
    monkeypatch.setenv('SMTP_USERNAME', 'user@example.com')
    monkeypatch.setenv('SMTP_PASSWORD', 'secret')
    monkeypatch.setattr('app.services.smtplib.SMTP', RecordingSMTP)
    monkeypatch.setattr('app.services.smtplib.SMTP_SSL', RecordingSMTP_SSL)

    assert send_substitution_email(_fake_db_with_recipients(), _absence(), 1) is True
    assert ('login', 'user@example.com', 'secret') in RecordingSMTP.calls


@pytest.mark.parametrize('exception', [
    smtplib.SMTPConnectError(421, 'cannot connect'),
    smtplib.SMTPAuthenticationError(535, 'bad credentials'),
    ConnectionRefusedError('connection refused'),
    TimeoutError('timed out'),
    OSError('network unreachable'),
])
def test_returns_false_when_smtp_transport_raises(monkeypatch, exception):
    monkeypatch.setenv('SMTP_HOST', 'smtp.example.com')
    monkeypatch.setenv('SMTP_FROM', 'no-reply@example.com')

    class RaisingSMTP(RecordingSMTP):
        def send_message(self, message):
            raise exception

    monkeypatch.setattr('app.services.smtplib.SMTP', RaisingSMTP)
    monkeypatch.setattr('app.services.smtplib.SMTP_SSL', RaisingSMTP)

    assert send_substitution_email(_fake_db_with_recipients(), _absence(), 1) is False


def test_email_body_falls_back_to_placeholder_when_group_and_classroom_missing(monkeypatch):
    monkeypatch.setenv('SMTP_HOST', 'smtp.example.com')
    monkeypatch.setenv('SMTP_FROM', 'no-reply@example.com')
    monkeypatch.setattr('app.services.smtplib.SMTP', RecordingSMTP)
    monkeypatch.setattr('app.services.smtplib.SMTP_SSL', RecordingSMTP_SSL)

    absence = _absence(class_group=None, classroom=None, observations=None)
    assert send_substitution_email(_fake_db_with_recipients(), absence, 1) is True
    sent_message = next(call[1] for call in RecordingSMTP.calls if call[0] == 'send_message')
    body = sent_message.get_content()
    assert 'no disponible' in body
