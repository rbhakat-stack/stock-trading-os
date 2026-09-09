"""Deterministic tests for the UI->domain conversion boundary
(engine.risk.positions.normalize_position_input) that fixed the Save
Positions bug: `st.data_editor` hands back a mix of Python int/float,
numpy.int64/float64, pandas NaN, and None — never guaranteed clean Python
primitives — and the exact reported row (SPY, 80, 100, 95, 98) must convert,
save, and reload correctly.

ROOT CAUSE OF THE REPORTED BUG (for the record, not exercised by these tests
directly — see test_positions_repository_save_semantics.py and the live
verification in the session report): app/pages/trade_planner.py's Save
Positions handler did `{p["symbol"] for p in pos_repo.list_positions(...)}`,
but list_positions() returns list[AccountPosition] (a frozen dataclass), not
dicts — `p["symbol"]` raised `TypeError: 'AccountPosition' object is not
subscriptable`. This only fired when the user already had at least one saved
position (list_positions() returned a non-empty list, so the set-
comprehension loop body actually executed) — i.e. on any "edit and re-save"
after the first successful save, which is why it looked like the very first
save "worked" in earlier testing but a real edit-then-save did not.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from engine.risk.positions import normalize_position_input


def _assert_valid(position, error, *, symbol, qty, price, stop=None, avg=None):
    assert error is None, error
    assert position is not None
    assert position.symbol == symbol
    assert position.quantity_signed == qty
    assert position.reference_price == price
    assert position.planned_stop_price == stop
    assert position.average_price == avg
    assert isinstance(position.symbol, str)
    assert isinstance(position.quantity_signed, float)
    assert isinstance(position.reference_price, float)
    assert position.planned_stop_price is None or isinstance(position.planned_stop_price, float)
    assert position.average_price is None or isinstance(position.average_price, float)


# ============================================================================
# The exact reported row: SPY, 80, 100, 95, 98
# ============================================================================


def test_exact_reported_row_python_primitives():
    position, error = normalize_position_input("SPY", 80, 100, 95, 98)
    _assert_valid(position, error, symbol="SPY", qty=80.0, price=100.0, stop=95.0, avg=98.0)


def test_exact_reported_row_python_floats():
    position, error = normalize_position_input("SPY", 80.0, 100.0, 95.0, 98.0)
    _assert_valid(position, error, symbol="SPY", qty=80.0, price=100.0, stop=95.0, avg=98.0)


def test_exact_reported_row_numpy_int64_and_float64():
    position, error = normalize_position_input(
        np.str_("SPY"), np.int64(80), np.float64(100.0), np.float64(95.0), np.float64(98.0),
    )
    _assert_valid(position, error, symbol="SPY", qty=80.0, price=100.0, stop=95.0, avg=98.0)


def test_exact_reported_row_via_pandas_dataframe_row():
    # Exactly how the value arrives in production: one row of a DataFrame
    # built the way st.data_editor would return it.
    df = pd.DataFrame({
        "symbol": ["SPY"], "quantity_signed": [80.0], "reference_price": [100.0],
        "planned_stop_price": [95.0], "average_price": [98.0],
    })
    row = df.iloc[0]
    position, error = normalize_position_input(
        row.get("symbol"), row.get("quantity_signed"), row.get("reference_price"),
        row.get("planned_stop_price"), row.get("average_price"),
    )
    _assert_valid(position, error, symbol="SPY", qty=80.0, price=100.0, stop=95.0, avg=98.0)
    assert type(position.quantity_signed) is float  # not numpy.float64


# ============================================================================
# Optional fields: blank/NaN/None -> None
# ============================================================================


def test_optional_fields_python_none():
    position, error = normalize_position_input("SPY", 80, 100, None, None)
    _assert_valid(position, error, symbol="SPY", qty=80.0, price=100.0, stop=None, avg=None)


def test_optional_fields_pandas_nan():
    position, error = normalize_position_input("SPY", 80, 100, float("nan"), float("nan"))
    _assert_valid(position, error, symbol="SPY", qty=80.0, price=100.0, stop=None, avg=None)


def test_optional_fields_numpy_nan():
    position, error = normalize_position_input("SPY", 80, 100, np.nan, np.nan)
    _assert_valid(position, error, symbol="SPY", qty=80.0, price=100.0, stop=None, avg=None)


def test_optional_fields_blank_string():
    position, error = normalize_position_input("SPY", 80, 100, "", "   ")
    _assert_valid(position, error, symbol="SPY", qty=80.0, price=100.0, stop=None, avg=None)


def test_optional_field_present_only_stop():
    position, error = normalize_position_input("SPY", 80, 100, 95, None)
    _assert_valid(position, error, symbol="SPY", qty=80.0, price=100.0, stop=95.0, avg=None)


# ============================================================================
# Symbol normalization
# ============================================================================


def test_symbol_stripped_and_uppercased():
    position, error = normalize_position_input("  spy  ", 80, 100)
    assert error is None
    assert position.symbol == "SPY"


def test_blank_symbol_with_data_is_an_error():
    position, error = normalize_position_input("", 80, 100)
    assert position is None
    assert error is not None
    assert "Symbol" in error


def test_whitespace_only_symbol_is_an_error():
    position, error = normalize_position_input("   ", 80, 100)
    assert position is None
    assert "Symbol" in error


# ============================================================================
# Entirely blank row -> silently skipped (not an error) — the data_editor
# "add row" placeholder before the user fills anything in.
# ============================================================================


def test_entirely_blank_row_is_silently_skipped():
    position, error = normalize_position_input(None, None, None, None, None)
    assert position is None
    assert error is None


def test_entirely_blank_row_with_nan():
    position, error = normalize_position_input(np.nan, np.nan, np.nan, np.nan, np.nan)
    assert position is None
    assert error is None


# ============================================================================
# Required-field validation errors, always naming the symbol
# ============================================================================


def test_missing_quantity_is_an_error_naming_the_symbol():
    position, error = normalize_position_input("SPY", None, 100)
    assert position is None
    assert error == "SPY: Signed Quantity is required."


def test_zero_quantity_is_an_error():
    position, error = normalize_position_input("SPY", 0, 100)
    assert position is None
    assert "cannot be zero" in error


def test_nan_quantity_is_finite_check_failure_not_a_crash():
    position, error = normalize_position_input("SPY", float("nan"), 100)
    assert position is None
    assert error is not None  # must not raise


def test_infinite_quantity_is_rejected():
    position, error = normalize_position_input("SPY", float("inf"), 100)
    assert position is None
    assert "finite" in error


def test_non_numeric_quantity_is_rejected_not_a_crash():
    position, error = normalize_position_input("SPY", "not-a-number", 100)
    assert position is None
    assert "number" in error


def test_missing_reference_price_is_an_error():
    position, error = normalize_position_input("SPY", 80, None)
    assert position is None
    assert error == "SPY: Reference Price is required."


def test_zero_reference_price_is_rejected():
    position, error = normalize_position_input("SPY", 80, 0)
    assert position is None
    assert "greater than 0" in error


def test_negative_reference_price_is_rejected():
    position, error = normalize_position_input("SPY", 80, -100)
    assert position is None
    assert "greater than 0" in error


def test_negative_planned_stop_is_rejected():
    position, error = normalize_position_input("SPY", 80, 100, -95)
    assert position is None
    assert "Planned Stop" in error


def test_negative_average_price_is_rejected():
    position, error = normalize_position_input("SPY", 80, 100, 95, -98)
    assert position is None
    assert "Average Price" in error


def test_non_numeric_planned_stop_is_rejected_not_a_crash():
    position, error = normalize_position_input("SPY", 80, 100, "abc")
    assert position is None
    assert "Planned Stop" in error


# ============================================================================
# Negative (SHORT) quantities work correctly
# ============================================================================


def test_short_position_negative_quantity():
    position, error = normalize_position_input("NVDA", -50, 120, 126, 123)
    _assert_valid(position, error, symbol="NVDA", qty=-50.0, price=120.0, stop=126.0, avg=123.0)


def test_short_position_numpy_negative_int64():
    position, error = normalize_position_input("NVDA", np.int64(-50), np.float64(120.0))
    assert error is None
    assert position.quantity_signed == -50.0
    assert isinstance(position.quantity_signed, float)
