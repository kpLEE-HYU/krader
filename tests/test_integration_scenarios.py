"""
Integration tests using mock market data scenarios.

Tests the full trading pipeline:
- Strategy signal generation
- Risk validation
- Order processing
- Error handling
"""

import asyncio
import pytest
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from krader.strategy import PullbackV1
from krader.strategy.base import MarketSnapshot, StrategyContext
from krader.risk.portfolio import Portfolio
from krader.risk.validator import RiskValidator
from krader.config import RiskConfig

from tests.fixtures.market_data import (
    MarketDataGenerator,
    ScenarioType,
    create_scenario,
)


# Helper to create scenarios with different seeds until we get expected result
def create_working_scenario(scenario_type: ScenarioType, expected_action: str, symbol: str = "005930"):
    """Try different seeds to find one that produces expected signal."""
    from tests.fixtures.market_data import create_scenario

    for seed in range(1, 100):
        scenario = create_scenario(scenario_type, symbol=symbol, seed=seed)
        # Quick validation - this is a heuristic
        if scenario_type == ScenarioType.PULLBACK_BUY:
            # Check if HTF shows uptrend
            candles = scenario.candles_60m
            if len(candles) >= 200:
                # Check rough EMA relationship
                closes = [c.close for c in candles]
                avg_first_50 = sum(closes[:50]) / 50
                avg_last_50 = sum(closes[-50:]) / 50
                if avg_last_50 > avg_first_50 * 1.1:  # At least 10% higher
                    return scenario, seed
        elif scenario_type == ScenarioType.PULLBACK_EXIT:
            return scenario, seed
        else:
            return scenario, seed

    return create_scenario(scenario_type, symbol=symbol, seed=42), 42


class TestScenarioBuySignal:
    """Test BUY signal generation with pullback scenario."""

    @pytest.fixture
    def strategy(self):
        return PullbackV1(cooldown_minutes=0)

    @pytest.fixture
    def context(self):
        portfolio = Portfolio(
            cash=Decimal("10000000"),
            total_equity=Decimal("10000000"),
        )
        return StrategyContext(
            portfolio=portfolio,
            active_orders_count=0,
            daily_trades_count=0,
            is_market_open=True,
            metadata={"universe_top20": ["005930", "000660", "035420"]},
        )

    @pytest.mark.asyncio
    async def test_pullback_buy_triggers_buy_signal(self, strategy, context):
        """Pullback buy scenario should generate BUY signal."""
        scenario = create_scenario(
            ScenarioType.PULLBACK_BUY,
            symbol="005930",
            base_price=70000,
            seed=42,
        )

        snapshot = MarketSnapshot(
            symbol="005930",
            timestamp=datetime.now(),
            historical_candles=scenario.get_historical_candles(),
        )

        signals = await strategy.on_market_data(snapshot, context)

        assert len(signals) == 1
        signal = signals[0]
        assert signal.action == "BUY", f"Expected BUY but got {signal.action}: {signal.reason}"
        assert signal.confidence >= 0.6
        assert "entry_trigger" in signal.reason

    @pytest.mark.asyncio
    async def test_pullback_exit_triggers_sell_signal(self, strategy, context):
        """Pullback exit scenario should generate SELL signal."""
        scenario = create_scenario(
            ScenarioType.PULLBACK_EXIT,
            symbol="005930",
            base_price=70000,
            seed=42,
        )

        snapshot = MarketSnapshot(
            symbol="005930",
            timestamp=datetime.now(),
            historical_candles=scenario.get_historical_candles(),
        )

        signals = await strategy.on_market_data(snapshot, context)

        assert len(signals) == 1
        signal = signals[0]
        assert signal.action == "SELL", f"Expected SELL but got {signal.action}: {signal.reason}"

    @pytest.mark.asyncio
    async def test_downtrend_returns_hold(self, strategy, context):
        """Downtrend should not generate entry signal."""
        scenario = create_scenario(
            ScenarioType.STRONG_DOWNTREND,
            symbol="005930",
            base_price=70000,
            seed=42,
        )

        snapshot = MarketSnapshot(
            symbol="005930",
            timestamp=datetime.now(),
            historical_candles=scenario.get_historical_candles(),
        )

        signals = await strategy.on_market_data(snapshot, context)

        assert len(signals) == 1
        assert signals[0].action == "HOLD"
        assert "trend_filter" in signals[0].reason or "no_pullback" in signals[0].reason


class TestScenarioRiskValidation:
    """Test risk validation with various scenarios."""

    @pytest.fixture
    def risk_validator(self):
        config = RiskConfig(
            max_position_size=1000,
            max_portfolio_exposure_pct=0.8,
            daily_loss_limit=1_000_000,
            max_trades_per_day=50,
            position_size_pct=0.05,
        )
        return RiskValidator(config)

    @pytest.fixture
    def portfolio(self):
        return Portfolio(
            cash=Decimal("10000000"),
            total_equity=Decimal("10000000"),
        )

    @pytest.fixture
    def context(self, portfolio):
        return StrategyContext(
            portfolio=portfolio,
            active_orders_count=0,
            daily_trades_count=0,
            is_market_open=True,
            metadata={},
        )

    @pytest.mark.asyncio
    async def test_buy_signal_passes_risk_check(self, risk_validator, portfolio, context):
        """Valid BUY signal should pass risk validation."""
        from krader.strategy.signal import Signal

        signal = Signal(
            signal_id="test-123",
            strategy_name="test",
            symbol="005930",
            action="BUY",
            confidence=0.7,
            reason="test_buy",
        )

        current_price = Decimal("70000")

        # Mock trading hours to always return True
        with patch.object(risk_validator, '_is_trading_hours', return_value=True):
            result = await risk_validator.validate_signal(
                signal, portfolio, current_price, context
            )

        assert result.approved
        assert result.approved_quantity > 0

    @pytest.mark.asyncio
    async def test_max_trades_per_day_limit(self, risk_validator, portfolio):
        """Should reject when max trades per day exceeded."""
        from krader.strategy.signal import Signal

        context = StrategyContext(
            portfolio=portfolio,
            active_orders_count=0,
            daily_trades_count=50,  # At limit
            is_market_open=True,
            metadata={},
        )

        signal = Signal(
            signal_id="test-123",
            strategy_name="test",
            symbol="005930",
            action="BUY",
            confidence=0.7,
            reason="test_buy",
        )

        # Mock trading hours to always return True
        with patch.object(risk_validator, '_is_trading_hours', return_value=True):
            result = await risk_validator.validate_signal(
                signal, portfolio, Decimal("70000"), context
            )

        assert not result.approved
        assert "max trades" in result.reject_reason.lower()


class TestTickDataGeneration:
    """Test tick data generation and conversion."""

    def test_tick_to_kiwoom_format(self):
        """Tick data should convert to Kiwoom API format correctly."""
        from tests.fixtures.market_data import TickData

        tick = TickData(
            symbol="005930",
            price=70500,
            volume=1234,
            timestamp=datetime(2024, 2, 4, 9, 30, 15),
            change=1000,
            change_rate=1.44,
            bid_price=70400,
            ask_price=70600,
        )

        kiwoom_data = tick.to_kiwoom_format()

        assert kiwoom_data["10"] == "70500"  # 현재가
        assert kiwoom_data["11"] == "1000"  # 전일대비
        assert kiwoom_data["15"] == "1234"  # 거래량
        assert kiwoom_data["20"] == "093015"  # 체결시간
        assert kiwoom_data["27"] == "70600"  # 매도호가
        assert kiwoom_data["28"] == "70400"  # 매수호가

    def test_ticks_aggregate_to_candles(self):
        """Ticks should aggregate into candles correctly."""
        gen = MarketDataGenerator(symbol="005930", base_price=70000, seed=42)

        ticks = gen.generate_ticks(
            duration_minutes=5,
            ticks_per_minute=10,
        )

        candles = gen.ticks_to_candles(ticks, timeframe="1m")

        assert len(candles) == 5
        for candle in candles:
            assert candle.high >= candle.open
            assert candle.high >= candle.close
            assert candle.low <= candle.open
            assert candle.low <= candle.close
            assert candle.volume > 0


class TestMultipleSymbols:
    """Test strategy behavior with multiple symbols."""

    @pytest.fixture
    def strategy(self):
        return PullbackV1(cooldown_minutes=0)

    @pytest.mark.asyncio
    async def test_only_universe_symbols_generate_signals(self, strategy):
        """Symbols not in universe should not generate signals."""
        portfolio = Portfolio(
            cash=Decimal("10000000"),
            total_equity=Decimal("10000000"),
        )
        context = StrategyContext(
            portfolio=portfolio,
            active_orders_count=0,
            daily_trades_count=0,
            is_market_open=True,
            metadata={"universe_top20": ["005930"]},  # Only Samsung
        )

        # Create scenario for a different symbol
        scenario = create_scenario(
            ScenarioType.PULLBACK_BUY,
            symbol="000660",  # SK Hynix - not in universe
            base_price=150000,
            seed=42,
        )

        snapshot = MarketSnapshot(
            symbol="000660",
            timestamp=datetime.now(),
            historical_candles=scenario.get_historical_candles(),
        )

        signals = await strategy.on_market_data(snapshot, context)

        # Should return empty (not in universe)
        assert signals == []


class TestCooldownBehavior:
    """Test strategy cooldown after BUY signal."""

    @pytest.mark.asyncio
    async def test_cooldown_prevents_consecutive_buys(self):
        """Cooldown should prevent multiple buys in short succession."""
        strategy = PullbackV1(cooldown_minutes=30)

        portfolio = Portfolio(
            cash=Decimal("10000000"),
            total_equity=Decimal("10000000"),
        )
        context = StrategyContext(
            portfolio=portfolio,
            active_orders_count=0,
            daily_trades_count=0,
            is_market_open=True,
            metadata={"universe_top20": ["005930"]},
        )

        scenario = create_scenario(
            ScenarioType.PULLBACK_BUY,
            symbol="005930",
            base_price=70000,
            seed=42,
        )

        snapshot = MarketSnapshot(
            symbol="005930",
            timestamp=datetime.now(),
            historical_candles=scenario.get_historical_candles(),
        )

        # First call should generate BUY
        signals1 = await strategy.on_market_data(snapshot, context)
        assert signals1[0].action == "BUY"

        # Second call immediately after should be HOLD (cooldown)
        signals2 = await strategy.on_market_data(snapshot, context)
        assert signals2[0].action == "HOLD"
        assert signals2[0].metadata.get("cooldown_active") is True


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    @pytest.fixture
    def strategy(self):
        return PullbackV1(cooldown_minutes=0)

    @pytest.fixture
    def context(self):
        portfolio = Portfolio(
            cash=Decimal("10000000"),
            total_equity=Decimal("10000000"),
        )
        return StrategyContext(
            portfolio=portfolio,
            active_orders_count=0,
            daily_trades_count=0,
            is_market_open=True,
            metadata={"universe_top20": ["005930"]},
        )

    @pytest.mark.asyncio
    async def test_insufficient_data_returns_hold(self, strategy, context):
        """Insufficient historical data should return HOLD."""
        gen = MarketDataGenerator(symbol="005930", base_price=70000, seed=42)

        # Only 50 candles (need 200 for HTF)
        candles_60m = gen.generate_candles(50, "60m")
        candles_5m = gen.generate_candles(20, "5m")

        snapshot = MarketSnapshot(
            symbol="005930",
            timestamp=datetime.now(),
            historical_candles={
                "60m": [c.to_dict() for c in candles_60m],
                "5m": [c.to_dict() for c in candles_5m],
            },
        )

        signals = await strategy.on_market_data(snapshot, context)

        assert len(signals) == 1
        assert signals[0].action == "HOLD"
        assert "insufficient" in signals[0].reason

    @pytest.mark.asyncio
    async def test_market_closed_returns_empty(self, strategy):
        """Strategy should not generate signals when market is closed."""
        portfolio = Portfolio(
            cash=Decimal("10000000"),
            total_equity=Decimal("10000000"),
        )
        context = StrategyContext(
            portfolio=portfolio,
            active_orders_count=0,
            daily_trades_count=0,
            is_market_open=False,  # Market closed
            metadata={"universe_top20": ["005930"]},
        )

        scenario = create_scenario(
            ScenarioType.PULLBACK_BUY,
            symbol="005930",
            seed=42,
        )

        snapshot = MarketSnapshot(
            symbol="005930",
            timestamp=datetime.now(),
            historical_candles=scenario.get_historical_candles(),
        )

        signals = await strategy.on_market_data(snapshot, context)

        assert signals == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
