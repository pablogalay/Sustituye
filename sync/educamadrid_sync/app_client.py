import logging

import httpx

logger = logging.getLogger(__name__)


class AppClientError(RuntimeError):
    pass


class AppClient:
    """Thin client for the Teacher Substitution Manager API (backend/app/main.py).
    Authenticates once as the configured administrator and reuses the same
    workflow (POST /absences) the frontend uses, so substitute assignment and
    email notification logic in backend/app/services.py is not duplicated here."""

    def __init__(self, base_url: str, admin_email: str, admin_password: str, timeout: float = 15.0) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout)
        self._admin_email = admin_email
        self._admin_password = admin_password

    def login(self) -> None:
        response = self._client.post(
            "/auth/login", json={"email": self._admin_email, "password": self._admin_password}
        )
        if response.status_code != 200:
            raise AppClientError(f"Could not authenticate against the app API: {response.status_code} {response.text}")
        token = response.json()["access_token"]
        self._client.headers["Authorization"] = f"Bearer {token}"
        logger.info("Authenticated against the app API as %s", self._admin_email)

    def get_teachers(self) -> list[dict]:
        response = self._client.get("/teachers")
        response.raise_for_status()
        return response.json()

    def get_timeslots(self) -> list[dict]:
        response = self._client.get("/timeslots")
        response.raise_for_status()
        return response.json()

    def create_absence(self, payload: dict) -> dict:
        response = self._client.post("/absences", json=payload)
        if response.status_code != 201:
            raise AppClientError(f"Could not create absence: {response.status_code} {response.text}")
        return response.json()

    def close(self) -> None:
        self._client.close()
