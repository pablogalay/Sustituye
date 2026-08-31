"""Unit tests for app.auth: password hashing, login(), and verify()."""
import os
os.environ.setdefault('DATABASE_URL', 'sqlite:///./test_auth.db')
os.environ.setdefault('JWT_SECRET', 'test-secret')

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import auth
from app.database import Base
from app.models import Teacher


@pytest.fixture()
def db():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


# --- hash_password / verify_password ----------------------------------------


def test_hash_password_roundtrip():
    stored = auth.hash_password('correct horse battery staple')
    assert auth.verify_password('correct horse battery staple', stored) is True


def test_verify_password_rejects_wrong_password():
    stored = auth.hash_password('correct horse battery staple')
    assert auth.verify_password('wrong password', stored) is False


@pytest.mark.parametrize('stored', [None, '', 'not-a-hash', 'pbkdf2_sha256$onlyonepart', 'a$b$c$d'])
def test_verify_password_handles_missing_or_malformed_hash(stored):
    assert auth.verify_password('anything', stored) is False


def test_verify_password_handles_invalid_base64_in_hash():
    # Well-formed shape ('$'-separated triplet) but the salt/digest are not
    # valid base64, exercising the except (ValueError, TypeError) branch.
    assert auth.verify_password('anything', 'pbkdf2_sha256$not-base64!!$not-base64!!') is False


@pytest.mark.parametrize('password', ['', 'x', 'a very long password ' * 20, 'ñ special chars 🔑'])
def test_hash_password_handles_edge_case_inputs(password):
    stored = auth.hash_password(password)
    assert auth.verify_password(password, stored) is True


# --- login() -----------------------------------------------------------------


def test_login_with_admin_credentials_returns_admin_claims(db, monkeypatch):
    monkeypatch.setenv('ADMIN_EMAIL', 'admin@school.local')
    monkeypatch.setenv('ADMIN_PASSWORD', 'admin123')
    result = auth.login('admin@school.local', 'admin123', db)
    claims = jwt.decode(result['access_token'], auth.SECRET, algorithms=['HS256'])
    assert claims['role'] == 'admin'
    assert claims['sub'] == 'admin@school.local'
    assert result['token_type'] == 'bearer'


def test_login_with_unknown_email_raises_401(db):
    with pytest.raises(HTTPException) as exc_info:
        auth.login('nobody@school.local', 'whatever', db)
    assert exc_info.value.status_code == 401


def test_login_with_inactive_teacher_raises_401(db):
    teacher = Teacher(
        first_name='Ana', last_name='García', email='ana@school.local',
        active=False, password_hash=auth.hash_password('s3cret123'),
    )
    db.add(teacher)
    db.commit()
    with pytest.raises(HTTPException) as exc_info:
        auth.login('ana@school.local', 's3cret123', db)
    assert exc_info.value.status_code == 401


def test_login_with_teacher_missing_password_hash_raises_401(db):
    teacher = Teacher(first_name='Ana', last_name='García', email='ana2@school.local', active=True, password_hash=None)
    db.add(teacher)
    db.commit()
    with pytest.raises(HTTPException) as exc_info:
        auth.login('ana2@school.local', 'whatever', db)
    assert exc_info.value.status_code == 401


def test_login_with_teacher_wrong_password_raises_401(db):
    teacher = Teacher(
        first_name='Ana', last_name='García', email='ana3@school.local',
        active=True, password_hash=auth.hash_password('s3cret123'),
    )
    db.add(teacher)
    db.commit()
    with pytest.raises(HTTPException) as exc_info:
        auth.login('ana3@school.local', 'wrong-password', db)
    assert exc_info.value.status_code == 401


def test_login_with_correct_teacher_credentials_returns_teacher_claims(db):
    teacher = Teacher(
        first_name='Ana', last_name='García', email='ana4@school.local',
        active=True, password_hash=auth.hash_password('s3cret123'),
    )
    db.add(teacher)
    db.commit()
    result = auth.login('ana4@school.local', 's3cret123', db)
    claims = jwt.decode(result['access_token'], auth.SECRET, algorithms=['HS256'])
    assert claims['role'] == 'teacher'
    assert claims['teacher_id'] == teacher.id


# --- verify() ------------------------------------------------------------------


def test_verify_accepts_a_token_issued_by_login(db):
    result = auth.login('admin@school.local', os.getenv('ADMIN_PASSWORD', 'admin123'), db)
    claims = auth.verify(result['access_token'])
    assert claims['role'] == 'admin'


@pytest.mark.parametrize('token', ['', 'not-a-jwt-at-all', 'a.b.c', 'Bearer garbage'])
def test_verify_rejects_malformed_tokens(token):
    with pytest.raises(HTTPException) as exc_info:
        auth.verify(token)
    assert exc_info.value.status_code == 401


def test_verify_rejects_token_signed_with_a_different_secret():
    forged = jwt.encode({'sub': 'x', 'role': 'admin'}, 'someone-elses-secret', algorithm='HS256')
    with pytest.raises(HTTPException) as exc_info:
        auth.verify(forged)
    assert exc_info.value.status_code == 401


def test_verify_rejects_expired_token():
    expired = jwt.encode(
        {'sub': 'x', 'role': 'admin', 'exp': datetime.now(timezone.utc) - timedelta(hours=1)},
        auth.SECRET, algorithm='HS256',
    )
    with pytest.raises(HTTPException) as exc_info:
        auth.verify(expired)
    assert exc_info.value.status_code == 401
