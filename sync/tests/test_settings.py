"""Unit tests for Settings: required env-var validation, whitespace-only
values, headless bool parsing edge cases, and the missing field_mapping.json
guard rail."""
import pytest

from educamadrid_sync import settings as settings_module
from educamadrid_sync.settings import Settings, get_settings

REQUIRED_VARS = {
    "EDUCAMADRID_USERNAME": "user123",
    "EDUCAMADRID_PASSWORD": "pass123",
    "EDUCAMADRID_SURVEY_URL": "https://formularios.educa.madrid.org/survey",
    "EDUCAMADRID_SURVEY_ID": "42",
}


@pytest.fixture()
def valid_env(monkeypatch):
    for name, value in REQUIRED_VARS.items():
        monkeypatch.setenv(name, value)
    for optional in ["APP_API_URL", "ADMIN_EMAIL", "ADMIN_PASSWORD", "EDUCAMADRID_HEADLESS"]:
        monkeypatch.delenv(optional, raising=False)


def test_settings_happy_path_uses_defaults_for_optional_vars(valid_env):
    settings = Settings()
    assert settings.educamadrid_username == "user123"
    assert settings.survey_id == "42"
    assert settings.app_api_url == "http://localhost:8000"
    assert settings.admin_email == "admin@school.local"
    assert settings.admin_password == "admin123"
    assert settings.headless is True


@pytest.mark.parametrize("missing_var", list(REQUIRED_VARS))
def test_settings_raises_when_a_required_var_is_missing(valid_env, monkeypatch, missing_var):
    monkeypatch.delenv(missing_var, raising=False)
    with pytest.raises(RuntimeError, match=missing_var):
        Settings()


@pytest.mark.parametrize("missing_var", list(REQUIRED_VARS))
def test_settings_raises_when_a_required_var_is_whitespace_only(valid_env, monkeypatch, missing_var):
    monkeypatch.setenv(missing_var, "   ")
    with pytest.raises(RuntimeError, match=missing_var):
        Settings()


@pytest.mark.parametrize("raw_value, expected", [
    ("true", True),
    ("TRUE", True),
    ("false", False),
    ("FALSE", False),
    ("0", False),
    ("no", False),
    ("NO", False),
    ("1", True),
    ("yes", True),  # not in the recognized falsy set {'0','false','no'} - defaults truthy
    ("", True),      # set-but-empty behaves like "unset" here, unlike the required vars
])
def test_headless_parsing_edge_cases(valid_env, monkeypatch, raw_value, expected):
    monkeypatch.setenv("EDUCAMADRID_HEADLESS", raw_value)
    assert Settings().headless is expected


def test_headless_defaults_true_when_unset(valid_env):
    assert Settings().headless is True


def test_app_api_url_strips_trailing_slash(valid_env, monkeypatch):
    monkeypatch.setenv("APP_API_URL", "http://localhost:9000/")
    assert Settings().app_api_url == "http://localhost:9000"


def test_missing_field_mapping_json_raises_with_a_helpful_message(valid_env, monkeypatch, tmp_path):
    monkeypatch.setattr(settings_module, "CONFIG_DIR", tmp_path)
    with pytest.raises(RuntimeError, match="field_mapping.json"):
        Settings()


def test_ensure_data_dir_creates_missing_nested_directory(valid_env, tmp_path):
    settings = Settings()
    settings.data_dir = tmp_path / "nested" / "data"
    assert not settings.data_dir.exists()
    settings.ensure_data_dir()
    assert settings.data_dir.is_dir()


def test_ensure_data_dir_is_a_no_op_when_already_present(valid_env, tmp_path):
    settings = Settings()
    settings.data_dir = tmp_path
    settings.ensure_data_dir()
    settings.ensure_data_dir()  # must not raise on a directory that already exists
    assert settings.data_dir.is_dir()


def test_get_settings_returns_a_settings_instance(valid_env):
    assert isinstance(get_settings(), Settings)
