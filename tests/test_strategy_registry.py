"""Tests for strategy registry."""

from krader.strategy.registry import (
    create_strategy,
    get_available_strategies,
    register_strategy,
)
from krader.strategy.base import BaseStrategy
from krader.strategy.pullback_v1 import PullbackV1


def test_get_available_strategies():
    """Test listing available strategies."""
    print("\n" + "=" * 60)
    print("Test: Get Available Strategies")
    print("=" * 60)

    strategies = get_available_strategies()
    print(f"Available strategies: {strategies}")

    assert isinstance(strategies, list)
    assert "pullback_v1" in strategies
    print("✅ PASS: pullback_v1 is available")


def test_create_strategy():
    """Test creating a strategy by name."""
    print("\n" + "=" * 60)
    print("Test: Create Strategy")
    print("=" * 60)

    strategy = create_strategy("pullback_v1")
    print(f"Created strategy: {strategy.name}")
    print(f"Type: {type(strategy)}")

    assert isinstance(strategy, BaseStrategy)
    assert isinstance(strategy, PullbackV1)
    assert strategy.name == "pullback_v1"
    print("✅ PASS: Strategy created successfully")


def test_create_unknown_strategy():
    """Test that unknown strategy raises error."""
    print("\n" + "=" * 60)
    print("Test: Create Unknown Strategy")
    print("=" * 60)

    try:
        create_strategy("unknown_strategy")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        print(f"Error message: {e}")
        assert "not found" in str(e)
        assert "pullback_v1" in str(e)  # Should list available
        print("✅ PASS: ValueError raised with helpful message")


def test_register_custom_strategy():
    """Test registering a custom strategy."""
    print("\n" + "=" * 60)
    print("Test: Register Custom Strategy")
    print("=" * 60)

    # Create a minimal test strategy
    class TestStrategy(BaseStrategy):
        @property
        def name(self) -> str:
            return "test_strategy"

        @property
        def symbols(self) -> list[str]:
            return []

        async def on_market_data(self, snapshot, context):
            return []

    # Register it
    register_strategy("test_custom", TestStrategy)
    print("Registered: test_custom")

    # Verify it's available
    strategies = get_available_strategies()
    print(f"Available strategies: {strategies}")
    assert "test_custom" in strategies

    # Create it
    strategy = create_strategy("test_custom")
    assert isinstance(strategy, TestStrategy)
    print("✅ PASS: Custom strategy registered and created")


def main():
    """Run all tests."""
    print("=" * 60)
    print("Strategy Registry Tests")
    print("=" * 60)

    test_get_available_strategies()
    test_create_strategy()
    test_create_unknown_strategy()
    test_register_custom_strategy()

    print("\n" + "=" * 60)
    print("All tests passed!")
    print("=" * 60)


if __name__ == "__main__":
    main()
