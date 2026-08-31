"""Tests for the internal FastAPI trigger service (server.py): the health
check, and every branch of the token gate + error handling around /run."""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from educamadrid_sync.server import app

client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_run_returns_500_when_token_not_configured(monkeypatch):
    monkeypatch.delenv("SYNC_TRIGGER_TOKEN", raising=False)
    response = client.post("/run", headers={"X-Sync-Token": "anything"})
    assert response.status_code == 500
    assert "not configured" in response.json()["detail"]


def test_run_returns_500_when_token_configured_as_only_whitespace(monkeypatch):
    monkeypatch.setenv("SYNC_TRIGGER_TOKEN", "   ")
    response = client.post("/run", headers={"X-Sync-Token": "anything"})
    assert response.status_code == 500


@pytest.mark.parametrize("header", [{}, {"X-Sync-Token": "wrong"}, {"X-Sync-Token": ""}, {"X-Sync-Token": "correct-token "}])
def test_run_returns_403_for_missing_or_wrong_token(monkeypatch, header):
    monkeypatch.setenv("SYNC_TRIGGER_TOKEN", "correct-token")
    response = client.post("/run", headers=header)
    assert response.status_code == 403


def test_run_returns_500_when_the_sync_itself_fails(monkeypatch):
    monkeypatch.setenv("SYNC_TRIGGER_TOKEN", "correct-token")
    with patch("educamadrid_sync.server.run", side_effect=RuntimeError("EducaMadrid login form changed")):
        response = client.post("/run", headers={"X-Sync-Token": "correct-token"})
    assert response.status_code == 500
    assert "EducaMadrid login form changed" in response.json()["detail"]


def test_run_returns_the_sync_result_on_success(monkeypatch):
    monkeypatch.setenv("SYNC_TRIGGER_TOKEN", "correct-token")
    fake_result = {"fetched": 3, "complete": 2, "imported": 2, "pending": 0}
    with patch("educamadrid_sync.server.run", return_value=fake_result):
        response = client.post("/run", headers={"X-Sync-Token": "correct-token"})
    assert response.status_code == 200
    assert response.json() == fake_result
