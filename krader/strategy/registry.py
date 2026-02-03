"""Strategy registry for runtime strategy selection.

This module provides a central registry for all available trading strategies.
Strategies are registered by name and can be instantiated at runtime based
on configuration.

To add a new strategy:
1. Create your strategy class in krader/strategy/my_strategy.py
2. Import it here and add to STRATEGY_REGISTRY
3. Set KRADER_STRATEGY=my_strategy_name in your config

Example:
    from krader.strategy.my_strategy import MyStrategy
    STRATEGY_REGISTRY["my_strategy"] = MyStrategy
"""

from typing import Callable, Type

from krader.strategy.base import BaseStrategy


# Type alias for strategy factory (class or callable that returns BaseStrategy)
StrategyFactory = Callable[[], BaseStrategy]

# Registry mapping strategy names to their factory classes
# Add new strategies here
STRATEGY_REGISTRY: dict[str, Type[BaseStrategy]] = {}


def _lazy_load_strategies() -> None:
    """Lazily load built-in strategies to avoid circular imports."""
    global STRATEGY_REGISTRY

    if STRATEGY_REGISTRY:
        return  # Already loaded

    # Import strategies here to avoid circular imports at module load time
    from krader.strategy.pullback_v1 import PullbackV1

    STRATEGY_REGISTRY.update({
        "pullback_v1": PullbackV1,
    })


def register_strategy(name: str, factory: Type[BaseStrategy]) -> None:
    """
    Register a strategy in the registry.

    Args:
        name: Unique strategy name (used in config)
        factory: Strategy class (must inherit from BaseStrategy)

    Raises:
        ValueError: If name is already registered
    """
    _lazy_load_strategies()

    if name in STRATEGY_REGISTRY:
        raise ValueError(f"Strategy '{name}' is already registered")

    if not (isinstance(factory, type) and issubclass(factory, BaseStrategy)):
        raise TypeError(f"Strategy factory must be a subclass of BaseStrategy")

    STRATEGY_REGISTRY[name] = factory


def get_available_strategies() -> list[str]:
    """
    Get list of all registered strategy names.

    Returns:
        List of strategy names sorted alphabetically
    """
    _lazy_load_strategies()
    return sorted(STRATEGY_REGISTRY.keys())


def create_strategy(name: str, **kwargs) -> BaseStrategy:
    """
    Create a strategy instance by name.

    Args:
        name: Strategy name (must be registered)
        **kwargs: Optional arguments passed to strategy constructor

    Returns:
        Instantiated strategy object

    Raises:
        ValueError: If strategy name is not found in registry
    """
    _lazy_load_strategies()

    if name not in STRATEGY_REGISTRY:
        available = get_available_strategies()
        raise ValueError(
            f"Strategy '{name}' not found. "
            f"Available strategies: {available}"
        )

    strategy_class = STRATEGY_REGISTRY[name]
    return strategy_class(**kwargs)
