import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "app"))

import pytest

from config import DEFAULT_LOCAL_APP_URL, get_app_url


def test_app_url_loads_from_environment(monkeypatch):
    monkeypatch.setenv("APP_URL", "https://trading.example.com")
    assert get_app_url() == "https://trading.example.com"


def test_app_url_trims_whitespace_and_trailing_slash(monkeypatch):
    monkeypatch.setenv("APP_URL", "  https://trading.example.com/  ")
    assert get_app_url() == "https://trading.example.com"


def test_local_fallback_when_unset(monkeypatch):
    # config.py's own module-level load_dotenv() only runs once at import time, so
    # this module doesn't re-read a local .env on every call — but set (not delete)
    # to keep this test robust regardless of what's already in the environment.
    monkeypatch.setenv("APP_URL", "")
    assert get_app_url() == DEFAULT_LOCAL_APP_URL == "http://localhost:8501"


def test_local_fallback_when_blank(monkeypatch):
    monkeypatch.setenv("APP_URL", "   ")
    assert get_app_url() == DEFAULT_LOCAL_APP_URL


@pytest.mark.parametrize(
    "bad_value",
    [
        "trading.example.com",       # missing scheme
        "ftp://trading.example.com", # wrong scheme
        "http://",                   # scheme with no host
        "javascript:alert(1)",       # not a URL at all
    ],
)
def test_invalid_app_url_is_rejected(monkeypatch, bad_value):
    monkeypatch.setenv("APP_URL", bad_value)
    with pytest.raises(RuntimeError):
        get_app_url()


def test_https_production_url_is_accepted(monkeypatch):
    monkeypatch.setenv("APP_URL", "https://my-trading-os.example.com")
    assert get_app_url() == "https://my-trading-os.example.com"
