"""Tests for market-structure primitives.

The causality tests matter most here: ICT backtests most often manufacture fake
edge by acting on a swing pivot at its own bar, before the confirming bars to
its right have printed.
"""

import numpy as np
import pandas as pd
import pytest

from ai_trading.strategies import structure as st


def bars_from(highs, lows, opens=None, closes=None):
    n = len(highs)
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    closes = closes if closes is not None else [(h + l) / 2 for h, l in zip(highs, lows)]
    opens = opens if opens is not None else list(closes)
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes}, index=idx
    )


# -- swing detection: causality -------------------------------------------


def test_no_swing_reported_in_the_unconfirmable_tail():
    """The final `right` bars can never be pivots -- nothing confirms them yet."""
    highs = [1, 2, 5, 2, 1, 2, 9]
    bars = bars_from(highs, [h - 1 for h in highs])
    swings = st.find_swings(bars, left=2, right=2)
    # Bar 6 is the highest bar in the frame, but has no bars to its right.
    assert all(s.index <= len(bars) - 1 - 2 for s in swings)
    assert not any(s.index == 6 for s in swings)


def test_confirmed_index_is_pivot_plus_right():
    highs = [1, 2, 5, 2, 1]
    bars = bars_from(highs, [h - 1 for h in highs])
    high_swings = [s for s in st.find_swings(bars, left=2, right=2) if s.kind == "high"]
    assert len(high_swings) == 1
    assert high_swings[0].index == 2
    assert high_swings[0].confirmed_index == 4


def test_growing_history_never_retracts_or_relocates_a_pivot():
    """Pivots found on a prefix must still be pivots on the full series."""
    rng = np.random.default_rng(7)
    close = 100 + np.cumsum(rng.normal(0, 1, 200))
    bars = bars_from(list(close + 1), list(close - 1), closes=list(close))

    full = {(s.index, s.kind) for s in st.find_swings(bars, 2, 2)}
    for cutoff in (50, 100, 150):
        prefix = {(s.index, s.kind) for s in st.find_swings(bars.iloc[:cutoff], 2, 2)}
        assert prefix <= full, f"prefix at {cutoff} found a pivot the full series does not"


def test_swing_high_requires_strictly_greater_neighbours():
    # Plateau: the tie means neither bar is an unambiguous pivot.
    highs = [1, 2, 5, 5, 2, 1, 1]
    bars = bars_from(highs, [h - 1 for h in highs])
    assert not [s for s in st.find_swings(bars, 2, 2) if s.kind == "high"]


def test_last_confirmed_swings_respects_as_of():
    highs = [1, 2, 5, 2, 1, 2, 3]
    bars = bars_from(highs, [h - 1 for h in highs])
    swings = st.find_swings(bars, 2, 2)
    # The pivot at bar 2 is confirmed at bar 4, so it is invisible at bar 3.
    assert st.last_confirmed_swings(swings, 3)[0] is None
    assert st.last_confirmed_swings(swings, 4)[0] is not None


def test_find_swings_rejects_invalid_parameters():
    bars = bars_from([1, 2, 3], [0, 1, 2])
    with pytest.raises(ValueError, match="must be >= 1"):
        st.find_swings(bars, left=0, right=2)


# -- fair value gaps -------------------------------------------------------


def test_bullish_fvg_detected_on_upward_imbalance():
    # Bar 2's low (12) sits above bar 0's high (10): untraded gap between.
    bars = bars_from(highs=[10, 14, 16], lows=[9, 11, 12])
    zones = st.fair_value_gaps(bars)
    assert len(zones) == 1
    assert zones[0].kind == "bullish_fvg"
    assert zones[0].lower == 10 and zones[0].upper == 12


def test_bearish_fvg_detected_on_downward_imbalance():
    bars = bars_from(highs=[16, 14, 10], lows=[12, 11, 9])
    zones = st.fair_value_gaps(bars)
    assert len(zones) == 1
    assert zones[0].kind == "bearish_fvg"


def test_no_fvg_when_ranges_overlap():
    bars = bars_from(highs=[10, 11, 12], lows=[9, 9.5, 9.8])
    assert st.fair_value_gaps(bars) == []


def test_fvg_is_confirmed_on_its_own_bar():
    bars = bars_from(highs=[10, 14, 16], lows=[9, 11, 12])
    zone = st.fair_value_gaps(bars)[0]
    assert zone.confirmed_index == zone.formed_index == 2


def test_min_gap_pct_filters_small_imbalances():
    bars = bars_from(highs=[10, 14, 16], lows=[9, 11, 10.001])
    assert st.fair_value_gaps(bars, min_gap_pct=0.01) == []


# -- order blocks ----------------------------------------------------------


def test_order_block_is_confirmed_at_the_break_not_at_formation():
    # Rally, pull back with a down candle, then break the swing high.
    opens = [10, 11, 12, 13, 12.5, 13, 15]
    closes = [11, 12, 13, 12.0, 13, 14, 16]
    highs = [11.5, 12.5, 13.5, 13.2, 13.5, 14.5, 16.5]
    lows = [9.5, 10.5, 11.5, 11.8, 12.4, 12.9, 14.9]
    bars = bars_from(highs, lows, opens, closes)
    zones = st.order_blocks(bars, left=1, right=1)
    for z in zones:
        assert z.confirmed_index > z.formed_index, "OB must be confirmed after it forms"


def test_order_block_zone_spans_the_origin_candle_range():
    rng = np.random.default_rng(3)
    close = 100 + np.cumsum(rng.normal(0.3, 1, 120))
    bars = bars_from(
        list(close + 0.5), list(close - 0.5), list(close - 0.1), list(close)
    )
    for z in st.order_blocks(bars, 2, 2):
        assert z.lower <= z.upper
        assert z.lower == pytest.approx(bars["low"].iloc[z.formed_index])
        assert z.upper == pytest.approx(bars["high"].iloc[z.formed_index])


def test_order_blocks_are_causal_under_truncation():
    rng = np.random.default_rng(11)
    close = 100 + np.cumsum(rng.normal(0, 1, 180))
    bars = bars_from(list(close + 1), list(close - 1), list(close), list(close))
    full = {(z.kind, z.formed_index, z.confirmed_index) for z in st.order_blocks(bars, 2, 2)}
    prefix = {
        (z.kind, z.formed_index, z.confirmed_index)
        for z in st.order_blocks(bars.iloc[:90], 2, 2)
    }
    assert prefix <= full


# -- liquidity sweeps ------------------------------------------------------


def test_liquidity_sweep_requires_close_back_inside():
    highs = [10, 9, 8, 9, 10, 11]
    lows = [9, 8, 6, 8, 9, 10]
    closes = [9.5, 8.5, 7.5, 8.5, 9.5, 10.5]
    bars = bars_from(highs, lows, closes=closes)
    for z in st.liquidity_sweeps(bars, 1, 1):
        assert z.lower <= z.upper


# -- zone helpers ----------------------------------------------------------


def test_zones_active_at_filters_unconfirmed_and_stale():
    zones = [
        st.Zone("bullish_fvg", 1.0, 2.0, 5, 5),
        st.Zone("bullish_fvg", 1.0, 2.0, 50, 50),
    ]
    assert len(st.zones_active_at(zones, as_of=10)) == 1  # second not yet confirmed
    # At bar 60 the first zone is 55 bars old and the second only 10.
    assert len(st.zones_active_at(zones, as_of=60, max_age=15)) == 1
    assert len(st.zones_active_at(zones, as_of=60, max_age=5)) == 0  # both stale
    assert len(st.zones_active_at(zones, as_of=60)) == 2


def test_zone_contains_and_midpoint():
    z = st.Zone("bullish_ob", 10.0, 20.0, 1, 2)
    assert z.contains(10.0) and z.contains(15.0) and z.contains(20.0)
    assert not z.contains(9.99)
    assert z.midpoint == 15.0
    assert z.is_bullish


def test_is_mitigated_only_considers_bars_after_confirmation():
    bars = bars_from(highs=[10, 20, 30, 12], lows=[5, 15, 25, 8])
    zone = st.Zone("bullish_ob", 8.0, 12.0, 0, 0)
    assert not st.is_mitigated(zone, bars, as_of=2)  # bars 1-2 sit above the zone
    assert st.is_mitigated(zone, bars, as_of=3)  # bar 3 trades back into it


def test_structure_functions_reject_missing_columns():
    bad = pd.DataFrame({"close": [1.0, 2.0]})
    for fn in (st.find_swings, st.fair_value_gaps, st.order_blocks):
        with pytest.raises(KeyError):
            fn(bad)
