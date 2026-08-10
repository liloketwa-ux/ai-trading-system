"""Tests for position sizing and portfolio risk limits."""

import pytest

from ai_trading.risk import RiskLimits, RiskManager


def test_size_risks_exactly_the_configured_fraction():
    """1% of 100k = 1000 risked; a $10 stop distance implies 100 units."""
    rm = RiskManager(RiskLimits(risk_per_trade=0.01, max_position_pct=1.0, max_leverage=10.0))
    decision = rm.size(equity=100_000.0, entry=100.0, stop=90.0)
    assert decision.approved
    assert decision.units == pytest.approx(100.0)
    assert decision.notional == pytest.approx(10_000.0)


def test_tighter_stop_yields_a_larger_position():
    rm = RiskManager(RiskLimits(risk_per_trade=0.01, max_position_pct=1.0, max_leverage=10.0))
    wide = rm.size(equity=100_000.0, entry=100.0, stop=90.0).units
    tight = rm.size(equity=100_000.0, entry=100.0, stop=98.0).units
    assert tight > wide


def test_position_cap_clamps_size():
    rm = RiskManager(RiskLimits(risk_per_trade=0.5, max_position_pct=0.25, max_leverage=10.0))
    decision = rm.size(equity=100_000.0, entry=100.0, stop=99.0)
    assert decision.notional == pytest.approx(25_000.0)
    assert "max_position_pct" in decision.reason


def test_leverage_headroom_clamps_size():
    rm = RiskManager(RiskLimits(risk_per_trade=0.5, max_position_pct=1.0, max_leverage=2.0))
    decision = rm.size(equity=10_000.0, entry=100.0, stop=99.0, existing_notional=15_000.0)
    # 2x on 10k = 20k gross cap; 15k already deployed leaves 5k.
    assert decision.notional == pytest.approx(5_000.0)
    assert "leverage" in decision.reason


def test_no_headroom_rejects_trade():
    rm = RiskManager(RiskLimits(max_leverage=2.0))
    decision = rm.size(equity=10_000.0, entry=100.0, stop=99.0, existing_notional=20_000.0)
    assert not decision.approved
    assert decision.units == 0.0
    assert "headroom" in decision.reason


def test_decision_is_falsy_when_rejected():
    rm = RiskManager(RiskLimits(max_leverage=1.0))
    assert not rm.size(equity=1_000.0, entry=10.0, stop=9.0, existing_notional=1_000.0)


# -- drawdown gating -------------------------------------------------------


def test_drawdown_tracks_against_peak_not_start():
    rm = RiskManager()
    rm.update_equity(100_000.0)
    rm.update_equity(120_000.0)  # new peak
    rm.update_equity(90_000.0)
    assert rm.current_drawdown(90_000.0) == pytest.approx(0.25)


def test_trading_halts_once_drawdown_limit_breached():
    rm = RiskManager(RiskLimits(max_drawdown=0.15))
    rm.update_equity(100_000.0)
    assert not rm.trading_halted(90_000.0)  # 10% down, still trading
    assert rm.trading_halted(85_000.0)  # 15% down, halted


def test_size_is_rejected_while_halted():
    rm = RiskManager(RiskLimits(max_drawdown=0.10))
    rm.update_equity(100_000.0)
    decision = rm.size(equity=80_000.0, entry=100.0, stop=90.0)
    assert not decision.approved
    assert "drawdown" in decision.reason


def test_drawdown_is_zero_before_any_equity_recorded():
    assert RiskManager().current_drawdown(50_000.0) == 0.0


# -- stops -----------------------------------------------------------------


def test_stop_is_below_entry_for_long_and_above_for_short():
    rm = RiskManager(RiskLimits(stop_atr_multiple=2.0))
    assert rm.stop_price(entry=100.0, side="long", atr=5.0) == pytest.approx(90.0)
    assert rm.stop_price(entry=100.0, side="short", atr=5.0) == pytest.approx(110.0)


def test_long_stop_never_goes_negative():
    rm = RiskManager(RiskLimits(stop_atr_multiple=10.0))
    assert rm.stop_price(entry=10.0, side="long", atr=5.0) == 0.0


def test_side_aliases_accepted():
    rm = RiskManager()
    assert rm.stop_price(100.0, "buy", 5.0) == rm.stop_price(100.0, "long", 5.0)
    assert rm.stop_price(100.0, "sell", 5.0) == rm.stop_price(100.0, "short", 5.0)


def test_unknown_side_rejected():
    with pytest.raises(ValueError, match="unrecognized side"):
        RiskManager().stop_price(100.0, "sideways", 5.0)


# -- validation ------------------------------------------------------------


def test_zero_risk_per_unit_rejected():
    with pytest.raises(ValueError, match="stop must differ"):
        RiskManager().size(equity=10_000.0, entry=100.0, stop=100.0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"risk_per_trade": 0.0},
        {"risk_per_trade": 1.5},
        {"max_leverage": 0.0},
        {"max_position_pct": 0.0},
        {"max_drawdown": 2.0},
        {"stop_atr_multiple": 0.0},
    ],
)
def test_invalid_limits_rejected(kwargs):
    with pytest.raises(ValueError):
        RiskLimits(**kwargs)


@pytest.mark.parametrize("bad", [{"equity": 0.0}, {"entry": 0.0}])
def test_invalid_size_inputs_rejected(bad):
    args = {"equity": 10_000.0, "entry": 100.0, "stop": 90.0} | bad
    with pytest.raises(ValueError):
        RiskManager().size(**args)
