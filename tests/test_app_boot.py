import pathlib

from streamlit.testing.v1 import AppTest

MAIN_PATH = str(pathlib.Path(__file__).resolve().parent.parent / "app" / "main.py")


def test_app_boots_and_lands_on_account_page_when_config_is_valid(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "dummy-anon-key")
    monkeypatch.setenv("APP_URL", "http://localhost:8501")

    at = AppTest.from_file(MAIN_PATH)
    at.run()

    assert not at.exception
    assert not at.error
    assert any(t.value == "Account" for t in at.title)


def test_app_shows_setup_screen_for_invalid_app_url_instead_of_crashing(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "dummy-anon-key")
    monkeypatch.setenv("APP_URL", "not-a-valid-url")

    at = AppTest.from_file(MAIN_PATH)
    at.run()

    assert not at.exception
    assert any(t.value == "Setup required" for t in at.title)
    assert any("APP_URL" in e.value for e in at.error)


def test_app_shows_setup_screen_when_supabase_config_missing(monkeypatch):
    # Set (not delete) to empty string: python-dotenv's load_dotenv() defaults to
    # override=False, so it would otherwise repopulate these from a real local
    # .env file on disk (e.g. one already set up for manual testing) and mask
    # the "missing config" case this test is meant to cover.
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "")

    at = AppTest.from_file(MAIN_PATH)
    at.run()

    assert not at.exception
    assert any(t.value == "Setup required" for t in at.title)
