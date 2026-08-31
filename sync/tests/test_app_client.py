"""Unit tests for AppClient: HTTP success/error paths and, per the "fallos de
infraestructura/red" requirement, connection failures (timeouts, dropped
connections, DNS/refused connections). No real network calls - httpx.MockTransport
stands in for the backend."""
import httpx
import pytest

from educamadrid_sync.app_client import AppClient, AppClientError


def _client(handler) -> AppClient:
    client = AppClient("https://app.example.test", "admin@school.local", "admin123")
    client._client = httpx.Client(base_url="https://app.example.test", transport=httpx.MockTransport(handler))
    return client


# --- login() -------------------------------------------------------------------


def test_login_success_sets_authorization_header():
    def handler(request):
        assert request.url.path == "/auth/login"
        return httpx.Response(200, json={"access_token": "tok123", "token_type": "bearer"})

    client = _client(handler)
    client.login()
    assert client._client.headers["Authorization"] == "Bearer tok123"


@pytest.mark.parametrize("status_code", [401, 403, 500])
def test_login_failure_raises_app_client_error(status_code):
    def handler(request):
        return httpx.Response(status_code, text="nope")

    client = _client(handler)
    with pytest.raises(AppClientError, match=str(status_code)):
        client.login()


def test_login_propagates_connection_refused():
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(httpx.ConnectError):
        _client(handler).login()


def test_login_propagates_timeout():
    def handler(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    with pytest.raises(httpx.ConnectTimeout):
        _client(handler).login()


# --- get_teachers() / get_timeslots() -------------------------------------------


def test_get_teachers_success():
    def handler(request):
        return httpx.Response(200, json=[{"id": 1, "email": "a@x.test"}])

    assert _client(handler).get_teachers() == [{"id": 1, "email": "a@x.test"}]


def test_get_teachers_handles_empty_response():
    def handler(request):
        return httpx.Response(200, json=[])

    assert _client(handler).get_teachers() == []


@pytest.mark.parametrize("status_code", [401, 403, 500, 502])
def test_get_teachers_raises_for_http_error_status(status_code):
    def handler(request):
        return httpx.Response(status_code, text="boom")

    with pytest.raises(httpx.HTTPStatusError):
        _client(handler).get_teachers()


def test_get_timeslots_success():
    def handler(request):
        return httpx.Response(200, json=[{"id": 1, "weekday": "Monday"}])

    assert _client(handler).get_timeslots() == [{"id": 1, "weekday": "Monday"}]


@pytest.mark.parametrize("status_code", [403, 500, 502])
def test_get_timeslots_raises_for_http_error_status(status_code):
    def handler(request):
        return httpx.Response(status_code)

    with pytest.raises(httpx.HTTPStatusError):
        _client(handler).get_timeslots()


def test_get_teachers_propagates_read_timeout_mid_response():
    def handler(request):
        raise httpx.ReadTimeout("timed out reading response", request=request)

    with pytest.raises(httpx.ReadTimeout):
        _client(handler).get_teachers()


# --- create_absence() -----------------------------------------------------------


def test_create_absence_success_returns_body():
    def handler(request):
        return httpx.Response(201, json={"id": 42})

    assert _client(handler).create_absence({"date": "2026-01-01"}) == {"id": 42}


@pytest.mark.parametrize("status_code", [409, 422, 500, 502])
def test_create_absence_failure_raises_app_client_error(status_code):
    def handler(request):
        return httpx.Response(status_code, text="rejected")

    with pytest.raises(AppClientError, match="rejected"):
        _client(handler).create_absence({})


def test_create_absence_propagates_dropped_connection_mid_transfer():
    def handler(request):
        raise httpx.RemoteProtocolError("peer closed connection without complete response", request=request)

    with pytest.raises(httpx.RemoteProtocolError):
        _client(handler).create_absence({})


def test_create_absence_handles_empty_payload():
    captured = {}

    def handler(request):
        captured["body"] = request.content
        return httpx.Response(201, json={"id": 1})

    _client(handler).create_absence({})
    assert captured["body"] == b"{}"


# --- close() ----------------------------------------------------------------------


def test_close_closes_the_underlying_http_client():
    def handler(request):
        return httpx.Response(200)

    client = _client(handler)
    client.close()
    assert client._client.is_closed
