from conftest import make_flat_df

from engine.features.volatility import atr, true_range


def test_true_range_zero_for_constant_price():
    df = make_flat_df([100.0] * 10)
    tr = true_range(df)
    assert tr.iloc[1:].eq(0).all()


def test_atr_positive_for_moving_price():
    closes = [100 + (i % 3) * 0.5 for i in range(30)]
    df = make_flat_df(closes)
    a = atr(df, period=14)
    assert a.dropna().gt(0).all()
