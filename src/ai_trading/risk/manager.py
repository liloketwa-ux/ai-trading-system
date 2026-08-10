"""Risk controls applied to signals before execution.

The risk manager is the last gate before an order reaches a broker. It answers
two questions: *how large may this position be?* and *are we permitted to trade
at all right now?* Both are deliberately conservative — every limit clamps
rather than raises, so a misconfigured strategy shrinks its exposure instead of
growing it.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["RiskLimits", "RiskDecision", "RiskManager"]


@dataclass(frozen=True)
class RiskLimits:
    """Portfolio and per-trade risk limits.

    Attributes:
        risk_per_trade: Fraction of equity risked between entry and stop
            (0.01 = 1%).
        max_leverage: Cap on gross notional as a multiple of equity.
        max_position_pct: Cap on a single position's notional as a fraction of
            equity.
        max_drawdown: Drawdown from peak equity at which new trades are halted
            (0.15 = 15%).
        stop_atr_multiple: Default stop distance in ATR multiples.
    """

    risk_per_trade: float = 0.01
    max_leverage: float = 5.0
    max_position_pct: float = 0.25
    max_drawdown: float = 0.15
    stop_atr_multiple: float = 2.0

    def __post_init__(self) -> None:
        if not 0.0 < self.risk_per_trade <= 1.0:
            raise ValueError(f"risk_per_trade must be in (0, 1], got {self.risk_per_trade}")
        if self.max_leverage <= 0:
            raise ValueError(f"max_leverage must be > 0, got {self.max_leverage}")
        if not 0.0 < self.max_position_pct <= 1.0:
            raise ValueError(f"max_position_pct must be in (0, 1], got {self.max_position_pct}")
        if not 0.0 < self.max_drawdown <= 1.0:
            raise ValueError(f"max_drawdown must be in (0, 1], got {self.max_drawdown}")
        if self.stop_atr_multiple <= 0:
            raise ValueError(f"stop_atr_multiple must be > 0, got {self.stop_atr_multiple}")


@dataclass(frozen=True)
class RiskDecision:
    """Outcome of a sizing request."""

    units: float
    notional: float
    approved: bool
    reason: str

    def __bool__(self) -> bool:
        return self.approved


class RiskManager:
    """Enforces position sizing and portfolio-level risk limits."""

    def __init__(self, limits: RiskLimits | None = None) -> None:
        self.limits = limits or RiskLimits()
        self._peak_equity: float | None = None

    # -- drawdown tracking -------------------------------------------------

    def update_equity(self, equity: float) -> None:
        """Record the latest equity so drawdown can be tracked against a peak."""
        if equity <= 0:
            raise ValueError(f"equity must be > 0, got {equity}")
        self._peak_equity = equity if self._peak_equity is None else max(self._peak_equity, equity)

    def current_drawdown(self, equity: float) -> float:
        """Drawdown from peak equity as a positive fraction (0.15 = 15% below peak)."""
        if self._peak_equity is None or self._peak_equity <= 0:
            return 0.0
        return max(0.0, 1.0 - equity / self._peak_equity)

    def trading_halted(self, equity: float) -> bool:
        """True when drawdown has breached the configured limit."""
        return self.current_drawdown(equity) >= self.limits.max_drawdown

    # -- sizing ------------------------------------------------------------

    def stop_price(self, entry: float, side: str, atr: float) -> float:
        """Protective stop placed ``stop_atr_multiple`` ATRs away from entry.

        The stop is floored at zero for long positions, since price cannot go
        negative.
        """
        if atr <= 0:
            raise ValueError(f"atr must be > 0, got {atr}")
        distance = self.limits.stop_atr_multiple * atr
        side_norm = _normalize_side(side)
        if side_norm == "long":
            return max(0.0, entry - distance)
        return entry + distance

    def size(
        self,
        equity: float,
        entry: float,
        stop: float,
        *,
        existing_notional: float = 0.0,
    ) -> RiskDecision:
        """Size a position from the distance between entry and stop.

        Units are chosen so that being stopped out costs approximately
        ``risk_per_trade`` of equity, then clamped by the per-position cap and
        the remaining gross-leverage headroom.

        Args:
            equity: Current account equity.
            entry: Intended entry price.
            stop: Protective stop price. Must differ from ``entry``.
            existing_notional: Gross notional already deployed, used to enforce
                the portfolio leverage cap.

        Returns:
            A :class:`RiskDecision`; ``approved`` is False (with ``units`` 0)
            when a limit forbids the trade.
        """
        if equity <= 0:
            raise ValueError(f"equity must be > 0, got {equity}")
        if entry <= 0:
            raise ValueError(f"entry must be > 0, got {entry}")
        if existing_notional < 0:
            raise ValueError(f"existing_notional must be >= 0, got {existing_notional}")

        risk_per_unit = abs(entry - stop)
        if risk_per_unit == 0:
            raise ValueError("stop must differ from entry (zero risk per unit)")

        if self.trading_halted(equity):
            return RiskDecision(
                0.0,
                0.0,
                False,
                f"drawdown {self.current_drawdown(equity):.2%} breached limit "
                f"{self.limits.max_drawdown:.2%}",
            )

        units = (equity * self.limits.risk_per_trade) / risk_per_unit
        reason = "sized by risk-per-trade"

        position_cap = equity * self.limits.max_position_pct
        if units * entry > position_cap:
            units = position_cap / entry
            reason = f"clamped to max_position_pct ({self.limits.max_position_pct:.0%})"

        headroom = equity * self.limits.max_leverage - existing_notional
        if headroom <= 0:
            return RiskDecision(0.0, 0.0, False, "no leverage headroom remaining")
        if units * entry > headroom:
            units = headroom / entry
            reason = f"clamped to leverage headroom ({self.limits.max_leverage:.1f}x)"

        if units <= 0:
            return RiskDecision(0.0, 0.0, False, "computed size rounded to zero")

        return RiskDecision(units, units * entry, True, reason)


def _normalize_side(side: str) -> str:
    value = side.strip().lower()
    if value in {"long", "buy"}:
        return "long"
    if value in {"short", "sell"}:
        return "short"
    raise ValueError(f"unrecognized side: {side!r}")
