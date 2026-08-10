"""Smoke tests: the scaffold imports and exposes its public interfaces."""

import ai_trading
from ai_trading.execution import Broker, OrderManager
from ai_trading.ingestion import DataSource
from ai_trading.nlp import SentimentModel
from ai_trading.strategies import Signal, Strategy


def test_version() -> None:
    assert ai_trading.__version__


def test_signal_dataclass() -> None:
    sig = Signal(symbol="BTC", weight=0.8, rationale="test", confidence=0.8)
    assert sig.symbol == "BTC"
    assert sig.side == "long"  # derived from the sign of weight
    assert 0.0 <= sig.confidence <= 1.0


def test_public_interfaces_exist() -> None:
    for obj in (DataSource, SentimentModel, Strategy, Broker, OrderManager):
        assert obj is not None
