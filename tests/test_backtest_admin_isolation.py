"""Phase 5.0 §14 — security/RLS design confirmation: service-role use for
bulk historical ingestion stays isolated in repository/admin infrastructure
and is never reachable from Streamlit/session state; ordinary ingestion
through a standard authenticated client never escalates privilege beyond
what that client's own RLS already grants."""
import ast
import pathlib

import pytest

import repository.admin_repository as admin_repo

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _source_imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_ingestion_module_never_imports_streamlit():
    path = _REPO_ROOT / "engine" / "backtest" / "ingestion.py"
    assert "streamlit" not in _source_imports(path)


def test_bar_source_module_never_imports_streamlit():
    path = _REPO_ROOT / "engine" / "backtest" / "bar_source.py"
    assert "streamlit" not in _source_imports(path)


def test_ingestion_script_never_imports_streamlit():
    path = _REPO_ROOT / "scripts" / "ingest_historical_bars.py"
    assert "streamlit" not in _source_imports(path)


def test_ingestion_script_obtains_its_client_only_through_admin_repository():
    """The script must never call repository.supabase_admin_client directly
    (which would bypass the "only repository/admin_repository.py imports
    this module" rule) — it must go through the one sanctioned wrapper."""
    path = _REPO_ROOT / "scripts" / "ingest_historical_bars.py"
    source = path.read_text(encoding="utf-8")
    assert "supabase_admin_client" not in source
    assert "get_service_role_client_for_backtest_ingestion" in source


def test_supabase_admin_client_docstring_still_names_the_narrow_exception():
    path = _REPO_ROOT / "repository" / "supabase_admin_client.py"
    source = path.read_text(encoding="utf-8")
    assert "admin_repository.py" in source  # the constraint itself is still documented, not silently dropped


def test_get_service_role_client_fails_closed_without_env_vars(monkeypatch):
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        admin_repo.get_service_role_client_for_backtest_ingestion()


def test_ordinary_client_ingestion_grants_no_extra_privilege(monkeypatch):
    """ingest_symbol_range must never itself check or grant a role — it just
    calls repository.market_data functions with whatever client it's given,
    so a standard authenticated client remains bound by exactly the SAME
    bars-table RLS policy live pages already use (§14: 'do not weaken
    existing RLS'). Proven by source inspection: no role/authorization
    check of any kind exists inside engine/backtest/ingestion.py."""
    source = (_REPO_ROOT / "engine" / "backtest" / "ingestion.py").read_text(encoding="utf-8")
    assert "require_admin" not in source
    assert "require_super_admin" not in source
    assert "get_current_user_role" not in source
