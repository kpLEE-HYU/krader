"""Tests for RiskValidator - max trades and transaction cost features."""

import asyncio
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from krader.config import RiskConfig
from krader.risk.portfolio import Portfolio
from krader.risk.validator import RiskValidator
from krader.strategy.base import StrategyContext
from krader.strategy.signal import Signal


def create_signal(
    action: str = "BUY",
    symbol: str = "005930",
    quantity: int = 10,
) -> Signal:
    """Create a test signal."""
    return Signal(
        signal_id=str(uuid4()),
        strategy_name="test",
        symbol=symbol,
        action=action,
        confidence=0.8,
        reason="test",
        suggested_quantity=quantity,
        metadata={},
        timestamp=datetime.now(),
    )


def create_portfolio(cash: Decimal = Decimal("10000000")) -> Portfolio:
    """Create a test portfolio."""
    return Portfolio(
        cash=cash,
        total_equity=cash,
    )


def create_context(daily_trades: int = 0) -> StrategyContext:
    """Create a test context."""
    return StrategyContext(
        portfolio=create_portfolio(),
        active_orders_count=0,
        daily_trades_count=daily_trades,
        metadata={},
    )


@pytest.mark.asyncio
async def test_max_trades_per_day_reject():
    """Test that signals are rejected when max trades reached."""
    print("\n" + "=" * 60)
    print("Test: Max Trades Per Day - Rejection")
    print("=" * 60)

    # Set trading hours to 0-24 to bypass trading hours check
    config = RiskConfig(
        max_trades_per_day=5,
        trading_start_hour=0,
        trading_end_hour=23,
        trading_end_minute=59,
    )
    validator = RiskValidator(config)

    signal = create_signal()
    portfolio = create_portfolio()
    context = create_context(daily_trades=5)  # Already at limit

    result = await validator.validate_signal(
        signal, portfolio, Decimal("50000"), context
    )

    print(f"Max trades: {config.max_trades_per_day}")
    print(f"Daily trades: {context.daily_trades_count}")
    print(f"Approved: {result.approved}")
    print(f"Reject reason: {result.reject_reason}")

    assert not result.approved
    assert "Max trades per day reached" in result.reject_reason
    print("✅ PASS: Signal rejected when max trades reached")


@pytest.mark.asyncio
async def test_max_trades_per_day_accept():
    """Test that signals are accepted when under max trades."""
    print("\n" + "=" * 60)
    print("Test: Max Trades Per Day - Acceptance")
    print("=" * 60)

    config = RiskConfig(
        max_trades_per_day=10,
        trading_start_hour=0,
        trading_end_hour=23,
        trading_end_minute=59,
    )
    validator = RiskValidator(config)

    signal = create_signal()
    portfolio = create_portfolio()
    context = create_context(daily_trades=5)  # Under limit

    result = await validator.validate_signal(
        signal, portfolio, Decimal("50000"), context
    )

    print(f"Max trades: {config.max_trades_per_day}")
    print(f"Daily trades: {context.daily_trades_count}")
    print(f"Approved: {result.approved}")
    print(f"Approved quantity: {result.approved_quantity}")

    assert result.approved
    print("✅ PASS: Signal accepted when under max trades")


@pytest.mark.asyncio
async def test_transaction_cost_cash_check():
    """Test that transaction cost is included in cash check."""
    print("\n" + "=" * 60)
    print("Test: Transaction Cost in Cash Check")
    print("=" * 60)

    # 1% transaction cost
    config = RiskConfig(
        transaction_cost_rate=0.01,
        trading_start_hour=0,
        trading_end_hour=23,
        trading_end_minute=59,
    )
    validator = RiskValidator(config)

    price = Decimal("50000")
    quantity = 200  # 200 shares @ 50000 = 10,000,000

    # With 1% fee, total cost = 10,000,000 * 1.01 = 10,100,000
    # Cash of exactly 10,000,000 should NOT be enough
    portfolio = Portfolio(
        cash=Decimal("10000000"),
        total_equity=Decimal("10000000"),
    )

    signal = create_signal(quantity=quantity)
    context = create_context()

    result = await validator.validate_signal(signal, portfolio, price, context)

    print(f"Price: {price}")
    print(f"Requested quantity: {quantity}")
    print(f"Notional: {price * quantity}")
    print(f"Transaction cost rate: {config.transaction_cost_rate:.2%}")
    print(f"Estimated fee: {float(price * quantity * Decimal(str(config.transaction_cost_rate))):.0f}")
    print(f"Total cost: {float(price * quantity * Decimal('1.01')):.0f}")
    print(f"Available cash: {portfolio.cash}")
    print(f"Approved: {result.approved}")
    print(f"Approved quantity: {result.approved_quantity}")

    # Should be reduced due to insufficient cash with fees
    assert result.approved
    assert result.approved_quantity < quantity
    print(f"✅ PASS: Quantity reduced from {quantity} to {result.approved_quantity} due to fees")


@pytest.mark.asyncio
async def test_transaction_cost_estimation():
    """Test transaction cost estimation calculation."""
    print("\n" + "=" * 60)
    print("Test: Transaction Cost Estimation")
    print("=" * 60)

    config = RiskConfig(
        transaction_cost_rate=0.00015,  # 0.015%
        trading_start_hour=0,
        trading_end_hour=23,
        trading_end_minute=59,
    )
    validator = RiskValidator(config)

    price = Decimal("50000")
    quantity = 100

    expected_fee = price * quantity * Decimal("0.00015")
    actual_fee = validator._estimated_transaction_cost(quantity, price)

    print(f"Price: {price}")
    print(f"Quantity: {quantity}")
    print(f"Notional: {price * quantity}")
    print(f"Rate: {config.transaction_cost_rate:.4%}")
    print(f"Expected fee: {expected_fee}")
    print(f"Actual fee: {actual_fee}")

    assert actual_fee == expected_fee
    print("✅ PASS: Transaction cost calculated correctly")


@pytest.mark.asyncio
async def test_backward_compatibility():
    """Test that validate_signal works without context (backward compat)."""
    print("\n" + "=" * 60)
    print("Test: Backward Compatibility (no context)")
    print("=" * 60)

    config = RiskConfig(
        trading_start_hour=0,
        trading_end_hour=23,
        trading_end_minute=59,
    )
    validator = RiskValidator(config)

    signal = create_signal()
    portfolio = create_portfolio()

    # Call without context parameter
    result = await validator.validate_signal(signal, portfolio, Decimal("50000"))

    print(f"Approved: {result.approved}")
    print(f"Approved quantity: {result.approved_quantity}")

    assert result.approved
    print("✅ PASS: validate_signal works without context (backward compatible)")


@pytest.mark.asyncio
async def test_position_size_calculation():
    """Test automatic position sizing when quantity is None."""
    print("\n" + "=" * 60)
    print("Test: Position Size Calculation (% of equity)")
    print("=" * 60)

    # 5% position size
    config = RiskConfig(
        position_size_pct=0.05,
        trading_start_hour=0,
        trading_end_hour=23,
        trading_end_minute=59,
    )
    validator = RiskValidator(config)

    # Signal with NO quantity (strategy delegates sizing)
    signal = create_signal(quantity=None)
    signal = Signal(
        signal_id=signal.signal_id,
        strategy_name="test",
        symbol="005930",
        action="BUY",
        confidence=0.8,
        reason="test",
        suggested_quantity=None,  # No quantity specified!
        metadata={},
        timestamp=datetime.now(),
    )

    # Portfolio with 10M equity
    portfolio = Portfolio(
        cash=Decimal("10000000"),
        total_equity=Decimal("10000000"),
    )

    price = Decimal("50000")  # 50,000 KRW per share
    context = create_context()

    result = await validator.validate_signal(signal, portfolio, price, context)

    # Expected: 5% of 10M = 500,000 / 50,000 = 10 shares
    expected_qty = int(10000000 * 0.05 / 50000)

    print(f"Equity: {portfolio.total_equity}")
    print(f"Position size %: {config.position_size_pct:.1%}")
    print(f"Price: {price}")
    print(f"Expected quantity: {expected_qty}")
    print(f"Approved: {result.approved}")
    print(f"Approved quantity: {result.approved_quantity}")

    assert result.approved
    assert result.approved_quantity == expected_qty
    print(f"✅ PASS: Position size correctly calculated as {expected_qty} shares (5% of 10M @ 50K)")


@pytest.mark.asyncio
async def test_position_size_respects_max():
    """Test that calculated position size respects max_position_size."""
    print("\n" + "=" * 60)
    print("Test: Position Size Respects Max Limit")
    print("=" * 60)

    # Large position size % but small max
    config = RiskConfig(
        position_size_pct=0.50,  # 50% would be 1000 shares
        max_position_size=100,   # But max is 100
        trading_start_hour=0,
        trading_end_hour=23,
        trading_end_minute=59,
    )
    validator = RiskValidator(config)

    signal = Signal(
        signal_id="test",
        strategy_name="test",
        symbol="005930",
        action="BUY",
        confidence=0.8,
        reason="test",
        suggested_quantity=None,
        metadata={},
        timestamp=datetime.now(),
    )

    portfolio = Portfolio(
        cash=Decimal("10000000"),
        total_equity=Decimal("10000000"),
    )

    price = Decimal("50000")
    context = create_context()

    result = await validator.validate_signal(signal, portfolio, price, context)

    print(f"Position size %: {config.position_size_pct:.0%}")
    print(f"Max position size: {config.max_position_size}")
    print(f"Calculated (uncapped): {int(10000000 * 0.50 / 50000)}")
    print(f"Approved quantity: {result.approved_quantity}")

    assert result.approved
    assert result.approved_quantity <= config.max_position_size
    print(f"✅ PASS: Position size capped at max_position_size ({config.max_position_size})")


async def main():
    """Run all tests."""
    print("=" * 60)
    print("RiskValidator Tests - New Features")
    print("=" * 60)

    await test_max_trades_per_day_reject()
    await test_max_trades_per_day_accept()
    await test_transaction_cost_cash_check()
    await test_transaction_cost_estimation()
    await test_backward_compatibility()
    await test_position_size_calculation()
    await test_position_size_respects_max()

    print("\n" + "=" * 60)
    print("All tests passed!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
