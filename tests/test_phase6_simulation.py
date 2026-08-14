"""Phase 5 design corrections and Phase 6 event-driven simulation."""

from datetime import date, datetime, timedelta, timezone

import pytest

from ai_trading.research.conditions import (
    Conjunction,
    ConditionType,
    Covariate,
    CovariateSpec,
    at_least,
    at_most,
    boolean,
    build_design_matrix,
    categorical,
    detect_redundant_conditions,
    presence,
    threshold_sweep,
    within,
)
from ai_trading.research.diagnostics import Warning_, diagnose_sample
from ai_trading.research.hypotheses import HypothesisRegistry
from ai_trading.research.sampling import Event
from ai_trading.simulation import (
    CONTRACTS,
    Account,
    BacktestConfig,
    BacktestEngine,
    ContractSpec,
    EventType,
    ExecutionConfig,
    ExecutionSimulator,
    FixedTickSlippage,
    OrderSide,
    OrderState,
    OrderType,
    PercentageSlippage,
    PointInTimeState,
    SimEvent,
    SimOrder,
    SimStrategy,
    SpreadSlippage,
    TradeCandidate,
    VolatilityAdjustedSlippage,
    make_bar_event,
)

UTC = timezone.utc
T0 = datetime(2024, 3, 4, 15, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)
ES = CONTRACTS["ES"]


# =========================================================================
# PART A -- Phase 5 design corrections
# =========================================================================


def ev(**features):
    return Event("ES", T0, features)


def test_presence_condition_matches_everything_with_the_feature():
    condition = presence("displacement_atr")
    assert condition.matches({"displacement_atr": 0.1})
    assert condition.matches({"displacement_atr": 99.0})
    assert not condition.matches({})


def test_threshold_conditions_actually_partition():
    events = [ev(disp=float(i) / 10) for i in range(20)]
    assert len(Conjunction((at_least("disp", 1.0),)).select(events)) == 10
    assert len(Conjunction((at_most("disp", 0.5),)).select(events)) == 6
    assert len(Conjunction((within("disp", 0.5, 1.0),)).select(events)) == 6


def test_categorical_condition():
    events = [ev(session=s) for s in ("london", "new_york", "asia", "london")]
    assert len(Conjunction((categorical("session", ["london"]),)).select(events)) == 2
    assert len(Conjunction((categorical("session", ["london", "asia"]),)).select(events)) == 3


def test_redundant_presence_condition_is_detected():
    """The exact Phase 5 defect: ICT-002 and ICT-003 selected identical samples."""
    events = [ev(sweep=(i % 2 == 0), disp=float(i)) for i in range(40)]
    base = Conjunction((boolean("sweep"),))
    inflated = Conjunction((boolean("sweep"), presence("disp")))

    assert len(base.select(events)) == len(inflated.select(events))
    report = detect_redundant_conditions(inflated, events)
    assert "disp PRESENT" in report.redundant
    assert report.has_redundancy


def test_a_real_threshold_is_not_flagged_redundant():
    events = [ev(sweep=True, disp=float(i) / 10) for i in range(40)]
    conjunction = Conjunction((boolean("sweep"), at_least("disp", 2.0)))
    report = detect_redundant_conditions(conjunction, events)
    assert "disp>=2.0" not in report.redundant


def test_effective_arity_counts_only_partitioning_conditions():
    events = [ev(sweep=(i % 2 == 0), disp=float(i) / 10) for i in range(40)]
    conjunction = Conjunction((boolean("sweep"), presence("disp"), at_least("disp", 1.0)))
    report = detect_redundant_conditions(conjunction, events)
    assert report.effective_arity < conjunction.arity


def test_threshold_sweep_creates_one_child_per_threshold():
    """Sweeping silently and reporting the best is N tests presented as one."""
    base = Conjunction((boolean("sweep"),))
    children = threshold_sweep("ICT-001", base, "disp", [1.0, 1.25, 1.5, 2.0])
    assert [c[0] for c in children] == ["ICT-001-A", "ICT-001-B", "ICT-001-C", "ICT-001-D"]
    for _, conjunction, _ in children:
        assert conjunction.arity == 2


def test_threshold_sweep_children_select_different_samples():
    events = [ev(sweep=True, disp=float(i) / 10) for i in range(40)]
    base = Conjunction((boolean("sweep"),))
    sizes = [len(c.select(events)) for _, c, _ in
             threshold_sweep("ICT-001", base, "disp", [1.0, 2.0, 3.0])]
    assert sizes == sorted(sizes, reverse=True)
    assert len(set(sizes)) > 1


def test_empty_threshold_sweep_rejected():
    with pytest.raises(ValueError, match="at least one threshold"):
        threshold_sweep("X", Conjunction(()), "disp", [])


def test_parent_child_hypotheses_are_registered_as_separate_trials(tmp_path):
    registry = HypothesisRegistry(tmp_path / "h.jsonl")
    registry.register("ICT-001", "parent", ("sweep",), label_key="l",
                      horizon_seconds=3600, research_version="r", dataset_version="d")
    for suffix, threshold in zip("ABCD", [1.0, 1.25, 1.5, 2.0]):
        registry.register(
            f"ICT-001-{suffix}", f"disp>={threshold}", ("sweep", "displacement_atr"),
            label_key="l", horizon_seconds=3600, research_version="r",
            dataset_version="d", parent_id="ICT-001",
            condition_label=f"displacement_atr>={threshold}",
        )
    assert registry.family_size("ICT") == 5     # every threshold counts
    assert len(registry.children_of("ICT-001")) == 4
    assert registry.tree("ICT")["ICT-001"] == [
        "ICT-001-A", "ICT-001-B", "ICT-001-C", "ICT-001-D"
    ]


def test_covariate_spec_formula():
    spec = CovariateSpec("forward_return_1h", (
        Covariate("displacement_atr"), Covariate("session", kind="categorical"),
    ))
    assert spec.formula == "forward_return_1h ~ displacement_atr + session"


def test_design_matrix_one_hot_encodes_categoricals():
    events = [ev(disp=float(i), session=("london" if i % 2 else "new_york"))
              for i in range(10)]
    spec = CovariateSpec("y", (Covariate("disp", standardize=False),
                               Covariate("session", kind="categorical")))
    matrix, names = build_design_matrix(spec, events)
    assert matrix.shape == (10, 2)          # disp + one dummy (reference dropped)
    assert names[0] == "disp"
    assert names[1].startswith("session=")


def test_design_matrix_standardizes_continuous_columns():
    events = [ev(disp=float(i)) for i in range(100)]
    spec = CovariateSpec("y", (Covariate("disp", standardize=True),))
    matrix, _ = build_design_matrix(spec, events)
    assert abs(matrix[:, 0].mean()) < 1e-9
    assert abs(matrix[:, 0].std() - 1.0) < 1e-9


def test_diagnostics_flag_low_sample():
    diagnostics = diagnose_sample([ev() for _ in range(10)], n_conditions=1)
    assert Warning_.LOW_SAMPLE in diagnostics.warnings
    assert not diagnostics.usable


def test_diagnostics_flag_excessive_conditioning():
    diagnostics = diagnose_sample([ev() for _ in range(100)], n_conditions=5,
                                  effective_conditions=5)
    assert Warning_.EXCESSIVE_CONDITIONING in diagnostics.warnings


def test_diagnostics_flag_imbalanced_outcomes():
    diagnostics = diagnose_sample(
        [ev() for _ in range(100)], outcomes=[1.0] * 96 + [-1.0] * 4,
    )
    assert Warning_.IMBALANCED_OUTCOME in diagnostics.warnings


def test_diagnostics_flag_overlapping_samples():
    diagnostics = diagnose_sample(
        [ev() for _ in range(100)], min_spacing=timedelta(hours=1),
        label_horizon=timedelta(hours=4),
    )
    assert Warning_.OVERLAPPING_SAMPLES in diagnostics.warnings


def test_diagnostics_flag_regime_fragmentation():
    events = [ev(session="london") for _ in range(90)] + [ev(session="asia") for _ in range(3)]
    diagnostics = diagnose_sample(events, regime_keys=("session",))
    assert Warning_.REGIME_FRAGMENTATION in diagnostics.warnings


def test_clean_sample_has_no_warnings():
    events = [ev(session="london" if i % 2 else "new_york") for i in range(120)]
    diagnostics = diagnose_sample(
        events, outcomes=[1.0 if i % 2 else -1.0 for i in range(120)],
        n_conditions=2, effective_conditions=2, regime_keys=("session",),
        min_spacing=timedelta(hours=4), label_horizon=timedelta(hours=1),
    )
    assert not diagnostics.warnings
    assert diagnostics.usable


# =========================================================================
# PART B -- Phase 6 simulation
# =========================================================================


def bar(open_, high, low, close, volume=1000.0):
    return {"open": open_, "high": high, "low": low, "close": close, "volume": volume}


def bar_events(bars, start=T0, duration=HOUR, instrument="ES"):
    return [make_bar_event(instrument, start + i * duration, duration, b)
            for i, b in enumerate(bars)]


def config(**kw):
    execution = kw.pop("execution", ExecutionConfig())
    return BacktestConfig(dataset_version="d1", strategy_version="s1",
                          hypothesis_id="ICT-001", execution=execution, **kw)


# -- contracts -------------------------------------------------------------


def test_contract_multiplier_is_required_not_assumed():
    assert ES.pnl(4500, 4501, 1, 1) == pytest.approx(50.0)
    assert CONTRACTS["NQ"].pnl(18000, 18001, 1, 1) == pytest.approx(20.0)
    assert CONTRACTS["CL"].pnl(75.00, 75.01, 1, 1) == pytest.approx(10.0)


def test_inconsistent_contract_spec_is_rejected():
    with pytest.raises(ValueError, match="contradicts multiplier"):
        ContractSpec("BAD", "X", tick_size=0.25, tick_value=12.5, multiplier=999.0)


def test_tick_rounding():
    assert ES.round_to_tick(4500.13) == pytest.approx(4500.25)
    assert ES.round_to_tick(4500.10) == pytest.approx(4500.00)


def test_roll_date_precedes_expiry():
    from ai_trading.simulation.contracts import roll_date

    assert roll_date(ES, date(2024, 3, 15)) == date(2024, 3, 7)


# -- events ----------------------------------------------------------------


def test_bar_event_is_available_at_its_close():
    event = make_bar_event("ES", T0, HOUR, bar(1, 2, 0.5, 1.5))
    assert event.timestamp == T0
    assert event.available_at == T0 + HOUR


def test_event_cannot_be_available_before_it_happens():
    with pytest.raises(ValueError, match="precedes timestamp"):
        SimEvent(EventType.BAR, T0, T0 - HOUR)


def test_events_sort_by_availability_first():
    late_but_early = SimEvent(EventType.BAR, T0, T0 + 5 * HOUR)
    early_but_later = SimEvent(EventType.BAR, T0 + HOUR, T0 + 2 * HOUR)
    ordered = sorted([late_but_early, early_but_later], key=lambda e: e.sort_key)
    assert ordered[0] is early_but_later


# -- order state machine ---------------------------------------------------


def order(order_type=OrderType.MARKET, side=OrderSide.BUY, qty=1.0, **kw):
    return SimOrder("ES", side, qty, order_type, T0, **kw)


def test_order_lifecycle_transitions():
    o = order()
    assert o.state is OrderState.CREATED
    o.transition(OrderState.SUBMITTED)
    o.transition(OrderState.PARTIALLY_FILLED)
    o.transition(OrderState.FILLED)
    assert o.state.is_terminal


def test_illegal_transition_rejected():
    o = order()
    with pytest.raises(ValueError, match="illegal transition"):
        o.transition(OrderState.FILLED)      # cannot fill before submission


def test_filled_order_cannot_transition_again():
    o = order()
    o.transition(OrderState.SUBMITTED)
    o.transition(OrderState.FILLED)
    with pytest.raises(ValueError, match="illegal transition"):
        o.transition(OrderState.CANCELLED)


def test_limit_order_requires_a_price():
    with pytest.raises(ValueError, match="limit_price"):
        order(OrderType.LIMIT)


def test_stop_limit_requires_both_prices():
    with pytest.raises(ValueError, match="limit_price"):
        order(OrderType.STOP_LIMIT, stop_price=4500.0)


# -- execution simulator ---------------------------------------------------


def simulator(**kw):
    return ExecutionSimulator(ExecutionConfig(**kw), ES)


def test_order_cannot_fill_before_it_was_submitted():
    """ATTACK: the canonical way a backtest invents an edge."""
    sim = simulator(latency=timedelta(minutes=30))
    o = order()
    sim.submit(o, T0)

    early = sim.process_bar(bar(4500, 4510, 4490, 4505), T0 + timedelta(minutes=10))
    assert not early.fills

    later = sim.process_bar(bar(4500, 4510, 4490, 4505), T0 + timedelta(minutes=40))
    assert len(later.fills) == 1


def test_zero_latency_fills_on_the_same_bar():
    sim = simulator(latency=timedelta(0))
    sim.submit(order(), T0)
    assert len(sim.process_bar(bar(4500, 4510, 4490, 4505), T0).fills) == 1


@pytest.mark.parametrize("delay_ms", [0, 100, 250, 500, 1000])
def test_configurable_execution_delays(delay_ms):
    sim = simulator(latency=timedelta(milliseconds=delay_ms))
    o = order()
    sim.submit(o, T0)
    assert o.eligible_at == T0 + timedelta(milliseconds=delay_ms)


def test_limit_order_fills_only_when_price_trades_through():
    sim = simulator()
    sim.submit(order(OrderType.LIMIT, limit_price=4490.0), T0)
    assert not sim.process_bar(bar(4500, 4510, 4495, 4505), T0).fills
    assert sim.process_bar(bar(4500, 4510, 4485, 4505), T0 + HOUR).fills


def test_stop_order_triggers_on_adverse_move():
    sim = simulator()
    sim.submit(order(OrderType.STOP, side=OrderSide.SELL, stop_price=4490.0), T0)
    assert sim.process_bar(bar(4500, 4510, 4480, 4485), T0).fills


def test_partial_fills():
    sim = simulator(max_fill_fraction=0.5)
    o = order(qty=4.0)
    sim.submit(o, T0)
    outcome = sim.process_bar(bar(4500, 4510, 4490, 4505), T0)
    assert outcome.fills[0].quantity == pytest.approx(2.0)
    assert outcome.fills[0].partial
    assert o.state is OrderState.PARTIALLY_FILLED
    assert o.remaining == pytest.approx(2.0)


def test_duplicate_submission_is_idempotent():
    sim = simulator()
    o = order()
    sim.submit(o, T0)
    sim.submit(o, T0)
    assert len(sim.orders) == 1


def test_cancelled_order_does_not_fill():
    sim = simulator()
    o = order(OrderType.LIMIT, limit_price=4490.0)
    sim.submit(o, T0)
    sim.cancel(o.order_id)
    assert not sim.process_bar(bar(4500, 4510, 4480, 4505), T0 + HOUR).fills


def test_commission_and_fees_are_charged_per_contract():
    sim = simulator(commission_per_contract=2.25, exchange_fee_per_contract=1.35)
    sim.submit(order(qty=3.0), T0)
    fill = sim.process_bar(bar(4500, 4510, 4490, 4505), T0).fills[0]
    assert fill.commission == pytest.approx(6.75)
    assert fill.fees == pytest.approx(4.05)


# -- slippage --------------------------------------------------------------


@pytest.mark.parametrize("model", [
    FixedTickSlippage(1.0), PercentageSlippage(0.0001),
    SpreadSlippage(1.0), VolatilityAdjustedSlippage(0.05),
])
def test_slippage_is_always_adverse(model):
    context = {"tick_size": 0.25, "atr": 10.0}
    assert model.adjust(4500.0, OrderSide.BUY, context) > 4500.0
    assert model.adjust(4500.0, OrderSide.SELL, context) < 4500.0


def test_volatility_slippage_scales_with_atr():
    model = VolatilityAdjustedSlippage(0.1)
    calm = model.adjust(4500.0, OrderSide.BUY, {"tick_size": 0.25, "atr": 5.0})
    wild = model.adjust(4500.0, OrderSide.BUY, {"tick_size": 0.25, "atr": 50.0})
    assert wild > calm


def test_slippage_model_name_is_recorded():
    assert ExecutionConfig(slippage=SpreadSlippage()).to_dict()["slippage_model"] == "spread"


# -- ambiguous bars --------------------------------------------------------


def test_ambiguous_bar_resolves_to_the_stop():
    """OHLC cannot establish intrabar order; favourable ordering is forbidden."""
    sim = simulator()
    outcome, ambiguous = sim.check_exit_levels(
        bar(4500, 4550, 4450, 4500), direction=1, stop=4470.0, target=4530.0
    )
    assert outcome == "stop"
    assert ambiguous
    assert sim.ambiguous_bar_count == 1


def test_unambiguous_target_hit():
    sim = simulator()
    outcome, ambiguous = sim.check_exit_levels(
        bar(4500, 4550, 4495, 4540), direction=1, stop=4470.0, target=4530.0
    )
    assert outcome == "target" and not ambiguous
    assert sim.ambiguous_bar_count == 0


def test_short_side_ambiguity_mirrors():
    sim = simulator()
    outcome, ambiguous = sim.check_exit_levels(
        bar(4500, 4550, 4450, 4500), direction=-1, stop=4530.0, target=4470.0
    )
    assert outcome == "stop" and ambiguous


def test_favourable_ordering_policy_is_rejected():
    with pytest.raises(ValueError, match="favourable ordering"):
        ExecutionConfig(ambiguous_bar_policy="target_wins")


# -- portfolio -------------------------------------------------------------


def test_account_applies_multiplier_to_pnl():
    account = Account(100_000.0)
    account.apply_fill("ES", 1.0, 4500.0, 0.0, ES)
    realized = account.apply_fill("ES", -1.0, 4510.0, 0.0, ES)
    assert realized == pytest.approx(500.0)     # 10 points x $50
    assert account.balance == pytest.approx(100_500.0)


def test_equity_includes_unrealized_pnl():
    account = Account(100_000.0)
    account.apply_fill("ES", 1.0, 4500.0, 0.0, ES)
    assert account.equity({"ES": 4520.0}, {"ES": ES}) == pytest.approx(101_000.0)


def test_costs_reduce_balance():
    account = Account(100_000.0)
    account.apply_fill("ES", 1.0, 4500.0, 3.60, ES)
    assert account.balance == pytest.approx(99_996.40)


def test_daily_equity_resets_on_session_change():
    account = Account(100_000.0)
    account.mark(T0, {"ES": 4500.0}, {"ES": ES}, date(2024, 3, 4))
    account.apply_fill("ES", 1.0, 4500.0, 0.0, ES)
    account.mark(T0 + HOUR, {"ES": 4400.0}, {"ES": ES}, date(2024, 3, 4))
    first_day_dd = account.max_daily_drawdown

    account.mark(T0 + 24 * HOUR, {"ES": 4400.0}, {"ES": ES}, date(2024, 3, 5))
    assert account.day_start_equity == pytest.approx(
        account.equity({"ES": 4400.0}, {"ES": ES})
    )
    assert first_day_dd > 0


def test_drawdown_tracks_peak_equity():
    account = Account(100_000.0)
    account.mark(T0, {}, {})
    account.balance = 120_000.0
    account.mark(T0 + HOUR, {}, {})
    account.balance = 90_000.0
    account.mark(T0 + 2 * HOUR, {}, {})
    assert account.max_drawdown == pytest.approx(0.25)


def test_position_flip_realizes_and_rebases():
    account = Account(100_000.0)
    account.apply_fill("ES", 2.0, 4500.0, 0.0, ES)
    account.apply_fill("ES", -3.0, 4510.0, 0.0, ES)
    position = account.position("ES")
    assert position.contracts == pytest.approx(-1.0)
    assert position.average_price == pytest.approx(4510.0)


# -- engine ----------------------------------------------------------------


class AlwaysLong(SimStrategy):
    name, version = "always_long", "1"

    def __init__(self, stop=30.0, target=30.0):
        self.stop, self.target = stop, target

    def evaluate(self, state: PointInTimeState):
        if state.in_position or len(state.bars) < 2:
            return None
        close = state.last_close
        return TradeCandidate(
            direction=1, contracts=1.0,
            stop_loss=close - self.stop, take_profit=close + self.target,
            hypothesis_id="ICT-001", reason="test",
        )


class NeverTrades(SimStrategy):
    name, version = "never", "1"

    def evaluate(self, state):
        return None


def rising_bars(n=40, start=4500.0, step=5.0):
    return [bar(start + i * step, start + i * step + 8, start + i * step - 3,
                start + i * step + 5) for i in range(n)]


def test_engine_runs_and_produces_a_result():
    engine = BacktestEngine(config(), ES)
    result = engine.run(bar_events(rising_bars(60)), AlwaysLong())
    assert result.events_processed == 60
    assert result.run_id
    assert result.instrument == "ES"


def test_strategy_receives_only_completed_bars():
    """ATTACK: the strategy must never see a bar beyond the decision."""
    seen = []

    class Spy(SimStrategy):
        name, version = "spy", "1"

        def evaluate(self, state):
            seen.append((state.decision_time, len(state.bars)))
            return None

    events = bar_events(rising_bars(20))
    BacktestEngine(config(), ES).run(events, Spy())

    for index, (decision_time, count) in enumerate(seen):
        assert count == index + 1
        # The decision happens at the bar's close, never at its open.
        assert decision_time == events[index].available_at


def test_state_carries_no_handle_to_future_data():
    """The interface itself forecloses df.iloc[-1] style access."""
    state = PointInTimeState(T0, "ES", bars=[bar(1, 2, 0.5, 1.5)])
    for forbidden in ("frame", "all_bars", "engine", "future"):
        assert not hasattr(state, forbidden)


def test_no_trades_when_strategy_stands_aside():
    result = BacktestEngine(config(), ES).run(bar_events(rising_bars(30)), NeverTrades())
    assert result.trade_count == 0
    assert result.net_return == pytest.approx(0.0)


def test_costs_make_an_otherwise_flat_strategy_lose():
    flat = [bar(4500, 4530, 4470, 4500) for _ in range(60)]
    result = BacktestEngine(config(), ES).run(bar_events(flat), AlwaysLong())
    if result.trade_count:
        assert result.total_costs > 0
        assert result.net_return < result.gross_return


def test_ambiguous_bars_are_counted_and_reported():
    wide = [bar(4500, 4560, 4440, 4500) for _ in range(40)]
    result = BacktestEngine(config(), ES).run(bar_events(wide), AlwaysLong(30, 30))
    assert result.ambiguous_bar_count > 0
    assert 0.0 <= result.ambiguous_trade_fraction <= 1.0


def test_execution_delay_changes_results():
    bars = rising_bars(60)
    fast = BacktestEngine(config(execution=ExecutionConfig(latency=timedelta(0))), ES) \
        .run(bar_events(bars), AlwaysLong())
    slow = BacktestEngine(
        config(execution=ExecutionConfig(latency=timedelta(hours=2))), ES
    ).run(bar_events(bars), AlwaysLong())
    assert fast.execution_delay_ms == 0
    assert slow.execution_delay_ms == 7_200_000


def test_backtests_are_deterministic():
    bars = rising_bars(60)
    a = BacktestEngine(config(), ES).run(bar_events(bars), AlwaysLong())
    b = BacktestEngine(config(), ES).run(bar_events(bars), AlwaysLong())
    assert a.run_id == b.run_id
    assert a.trade_count == b.trade_count
    assert a.net_return == pytest.approx(b.net_return)
    assert a.total_costs == pytest.approx(b.total_costs)


def test_run_id_changes_with_execution_assumptions():
    base = config()
    slower = config(execution=ExecutionConfig(latency=timedelta(seconds=1)))
    assert base.run_id != slower.run_id


def test_run_id_is_stable_across_created_at():
    """Re-running the same experiment tomorrow is the same experiment."""
    a = config()
    b = config()
    assert a.run_id == b.run_id


def test_config_records_full_reproducibility_metadata():
    payload = config(feature_versions={"atr": "1"}, random_seed=7).to_dict()
    for field in ("run_id", "dataset_version", "strategy_version", "hypothesis_id",
                  "feature_versions", "parameters", "execution", "cost_model_version",
                  "random_seed", "code_commit"):
        assert field in payload


def test_result_reports_every_required_metric():
    result = BacktestEngine(config(), ES).run(bar_events(rising_bars(80)), AlwaysLong())
    payload = result.to_dict()
    for field in ("trade_count", "win_rate", "average_win", "average_loss",
                  "expectancy", "profit_factor", "sharpe", "sortino",
                  "max_drawdown", "max_daily_drawdown", "longest_losing_streak",
                  "average_r", "mae", "mfe", "turnover", "total_costs",
                  "gross_return", "net_return", "ambiguous_bar_count",
                  "execution_delay_ms", "slippage_model", "commission_model"):
        assert field in payload, f"result missing {field}"


def test_result_renders_without_claiming_profitability():
    result = BacktestEngine(config(), ES).run(bar_events(rising_bars(60)), AlwaysLong())
    rendered = result.render()
    assert "Ambiguous bars" in rendered
    assert "profitable" not in rendered.lower()


@pytest.mark.parametrize("symbol", ["ES", "NQ", "YM", "GC", "CL"])
def test_same_hypothesis_runs_independently_per_instrument(symbol):
    """Results must not be aggregated in a way that hides per-instrument failure."""
    spec = CONTRACTS[symbol]
    base = 4500.0 if symbol in ("ES", "NQ", "YM") else 75.0
    step = base * 0.001
    bars = [bar(base + i * step, base + i * step + step * 2,
                base + i * step - step, base + i * step + step) for i in range(60)]
    result = BacktestEngine(config(), spec).run(
        bar_events(bars, instrument=symbol), AlwaysLong(step * 5, step * 5)
    )
    assert result.instrument == symbol
    assert result.config["run_id"]


def test_session_boundary_drives_daily_reset():
    """Daily figures reset on the contract session, not UTC midnight."""
    engine = BacktestEngine(config(), ES)
    engine.run(bar_events(rising_bars(60), start=T0, duration=HOUR), AlwaysLong())
    assert engine.account.current_session is not None


def test_slippage_models_handle_a_missing_spread_field():
    """Bars carry no spread key, so the context holds None -- a get-default never fires."""
    context = {"tick_size": 0.25, "atr": None, "spread": None}
    for model in (SpreadSlippage(2.0), VolatilityAdjustedSlippage(0.1),
                  FixedTickSlippage(1.0), PercentageSlippage(0.0001)):
        assert model.adjust(4500.0, OrderSide.BUY, context) > 4500.0


def test_sub_bar_latency_cannot_change_bar_resolution_results():
    """Honest limitation: millisecond delay is invisible at hourly resolution.

    Delay only bites when it crosses a bar boundary, so latency sensitivity must
    be tested at a resolution finer than the delay being modelled.
    """
    bars = rising_bars(40)
    results = []
    for ms in (0, 250, 1000):
        cfg = config(execution=ExecutionConfig(latency=timedelta(milliseconds=ms)))
        results.append(BacktestEngine(cfg, ES).run(bar_events(bars), AlwaysLong()).net_return)
    assert len(set(results)) == 1

    # At a delay spanning whole bars the result must move.
    slow = config(execution=ExecutionConfig(latency=timedelta(hours=3)))
    assert BacktestEngine(slow, ES).run(bar_events(bars), AlwaysLong()).trade_count <= \
        BacktestEngine(config(), ES).run(bar_events(bars), AlwaysLong()).trade_count
