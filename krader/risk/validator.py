"""Risk validation for trading signals."""

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from krader.config import RiskConfig
from krader.risk.portfolio import Portfolio

if TYPE_CHECKING:
    from krader.strategy.base import StrategyContext
    from krader.strategy.signal import Signal

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of signal validation."""

    approved: bool
    approved_quantity: int
    reject_reason: str | None = None

    @classmethod
    def accept(cls, quantity: int) -> "ValidationResult":
        """Create an approved result."""
        return cls(approved=True, approved_quantity=quantity)

    @classmethod
    def reject(cls, reason: str) -> "ValidationResult":
        """Create a rejected result."""
        return cls(approved=False, approved_quantity=0, reject_reason=reason)


class RiskValidator:
    """Validates trading signals against risk rules."""

    def __init__(
        self,
        config: RiskConfig,
        kill_switch_active: bool = False,
    ) -> None:
        self._config = config
        self._kill_switch_active = kill_switch_active

    @property
    def kill_switch_active(self) -> bool:
        """Check if kill switch is active."""
        return self._kill_switch_active

    def activate_kill_switch(self) -> None:
        """Activate kill switch - no new orders allowed."""
        self._kill_switch_active = True
        logger.warning("Kill switch ACTIVATED - no new orders will be placed")

    def deactivate_kill_switch(self) -> None:
        """Deactivate kill switch."""
        self._kill_switch_active = False
        logger.info("Kill switch deactivated")

    async def validate_signal(
        self,
        signal: "Signal",
        portfolio: Portfolio,
        current_price: Decimal | None = None,
        context: "StrategyContext | None" = None,
    ) -> ValidationResult:
        """
        Validate a trading signal against risk rules.

        Args:
            signal: The trading signal to validate
            portfolio: Current portfolio state
            current_price: Current market price (for value calculations)
            context: Strategy context with daily_trades_count (optional for backward compat)

        Returns:
            ValidationResult with approved quantity or rejection reason
        """
        if self._kill_switch_active:
            return ValidationResult.reject("Kill switch is active")

        if signal.action == "HOLD":
            return ValidationResult.reject("HOLD signals do not generate orders")

        if not self._is_trading_hours():
            return ValidationResult.reject("Outside trading hours")

        # Calculate quantity if not provided by strategy
        if signal.suggested_quantity is None or signal.suggested_quantity <= 0:
            if current_price is None or current_price <= 0:
                return ValidationResult.reject(
                    "Cannot calculate position size: no price available"
                )
            calculated_qty = self._calculate_position_size(portfolio, current_price)
            if calculated_qty <= 0:
                return ValidationResult.reject(
                    "Calculated position size is zero (insufficient equity)"
                )
            requested_qty = calculated_qty
            logger.info(
                "Position size calculated: %d shares (%.1f%% of %.0f equity @ %.0f)",
                calculated_qty,
                self._config.position_size_pct * 100,
                float(portfolio.total_equity),
                float(current_price),
            )
        else:
            requested_qty = signal.suggested_quantity

        # Check max trades per day limit
        if context is not None:
            trades_result = self._check_max_trades_per_day(context)
            if not trades_result.approved:
                return trades_result

        position_result = self._check_position_size(
            signal.symbol,
            signal.action,
            requested_qty,
            portfolio,
        )
        if not position_result.approved:
            return position_result

        exposure_result = self._check_portfolio_exposure(
            requested_qty,
            current_price,
            portfolio,
        )
        if not exposure_result.approved:
            return exposure_result

        if signal.action == "BUY":
            cash_result = self._check_available_cash(
                requested_qty,
                current_price,
                portfolio,
            )
            if not cash_result.approved:
                return cash_result

        daily_loss_result = self._check_daily_loss_limit(portfolio)
        if not daily_loss_result.approved:
            return daily_loss_result

        final_qty = min(
            requested_qty,
            position_result.approved_quantity,
            exposure_result.approved_quantity,
        )

        if signal.action == "BUY":
            final_qty = min(final_qty, self._max_buyable_quantity(current_price, portfolio))

        if final_qty <= 0:
            return ValidationResult.reject("Approved quantity is zero")

        # Log transaction cost estimate
        if current_price is not None:
            estimated_fee = self._estimated_transaction_cost(final_qty, current_price)
            logger.info(
                "Signal approved: %s %s qty=%d (requested=%d), estimated_fee=%.2f",
                signal.action,
                signal.symbol,
                final_qty,
                requested_qty,
                float(estimated_fee),
            )
        else:
            logger.info(
                "Signal approved: %s %s qty=%d (requested=%d)",
                signal.action,
                signal.symbol,
                final_qty,
                requested_qty,
            )

        return ValidationResult.accept(final_qty)

    def _calculate_position_size(
        self,
        portfolio: Portfolio,
        current_price: Decimal,
    ) -> int:
        """
        Calculate position size based on percentage of equity.

        Formula: quantity = (equity * position_size_pct) / price

        The result is further limited by:
        - max_position_size (max shares per symbol)
        - Available cash (accounting for transaction costs)

        Args:
            portfolio: Current portfolio state
            current_price: Current market price

        Returns:
            Calculated quantity (shares), minimum 0
        """
        if current_price <= 0 or portfolio.total_equity <= 0:
            return 0

        # Calculate target position value
        pct = Decimal(str(self._config.position_size_pct))
        target_value = portfolio.total_equity * pct

        # Calculate quantity from target value
        quantity = int(target_value / current_price)

        # Limit by max_position_size config
        quantity = min(quantity, self._config.max_position_size)

        return max(0, quantity)

    def _check_max_trades_per_day(
        self,
        context: "StrategyContext",
    ) -> ValidationResult:
        """Check if max trades per day limit has been reached."""
        max_trades = self._config.max_trades_per_day
        current_trades = context.daily_trades_count

        if current_trades >= max_trades:
            logger.warning(
                "Max trades per day reached: %d/%d",
                current_trades,
                max_trades,
            )
            return ValidationResult.reject(
                f"Max trades per day reached ({current_trades}/{max_trades})"
            )

        return ValidationResult.accept(999999)

    def _estimated_transaction_cost(
        self,
        quantity: int,
        current_price: Decimal,
    ) -> Decimal:
        """
        Calculate estimated transaction cost.

        This is an approximation using a flat percentage rate.
        Actual broker fees may vary based on order type, size, etc.

        Args:
            quantity: Order quantity
            current_price: Current market price

        Returns:
            Estimated transaction cost in currency units
        """
        notional = current_price * quantity
        rate = Decimal(str(self._config.transaction_cost_rate))
        return notional * rate

    def _is_trading_hours(self) -> bool:
        """Check if current time is within trading hours."""
        now = datetime.now()
        start_time = now.replace(
            hour=self._config.trading_start_hour,
            minute=self._config.trading_start_minute,
            second=0,
            microsecond=0,
        )
        end_time = now.replace(
            hour=self._config.trading_end_hour,
            minute=self._config.trading_end_minute,
            second=0,
            microsecond=0,
        )
        return start_time <= now <= end_time

    def _check_position_size(
        self,
        symbol: str,
        action: str,
        quantity: int,
        portfolio: Portfolio,
    ) -> ValidationResult:
        """Check if order would exceed max position size."""
        current_qty = portfolio.get_position_quantity(symbol)

        if action == "BUY":
            resulting_qty = current_qty + quantity
        else:  # SELL
            resulting_qty = current_qty - quantity

        if abs(resulting_qty) > self._config.max_position_size:
            max_allowed = self._config.max_position_size - abs(current_qty)
            if max_allowed <= 0:
                return ValidationResult.reject(
                    f"Position size limit reached for {symbol}"
                )
            return ValidationResult.accept(max_allowed)

        return ValidationResult.accept(quantity)

    def _check_portfolio_exposure(
        self,
        quantity: int,
        current_price: Decimal | None,
        portfolio: Portfolio,
    ) -> ValidationResult:
        """Check if order would exceed max portfolio exposure."""
        if current_price is None or portfolio.total_equity <= 0:
            return ValidationResult.accept(quantity)

        order_value = current_price * quantity
        new_exposure = (portfolio.total_position_value + order_value) / portfolio.total_equity

        if new_exposure > Decimal(str(self._config.max_portfolio_exposure_pct)):
            max_additional_value = (
                portfolio.total_equity * Decimal(str(self._config.max_portfolio_exposure_pct))
                - portfolio.total_position_value
            )
            if max_additional_value <= 0:
                return ValidationResult.reject("Portfolio exposure limit reached")
            max_qty = int(max_additional_value / current_price)
            if max_qty <= 0:
                return ValidationResult.reject("Portfolio exposure limit reached")
            return ValidationResult.accept(max_qty)

        return ValidationResult.accept(quantity)

    def _check_available_cash(
        self,
        quantity: int,
        current_price: Decimal | None,
        portfolio: Portfolio,
    ) -> ValidationResult:
        """
        Check if there's enough cash for the order including transaction costs.

        The total cost includes:
        - Order notional value (price * quantity)
        - Estimated transaction cost (notional * transaction_cost_rate)
        """
        if current_price is None:
            return ValidationResult.accept(quantity)

        order_value = current_price * quantity
        estimated_fee = self._estimated_transaction_cost(quantity, current_price)
        total_cost = order_value + estimated_fee

        if total_cost > portfolio.cash:
            # Calculate max quantity accounting for fees
            # total = price * qty * (1 + rate)
            # qty = cash / (price * (1 + rate))
            rate = Decimal(str(self._config.transaction_cost_rate))
            effective_price = current_price * (Decimal("1") + rate)
            max_qty = int(portfolio.cash / effective_price)

            if max_qty <= 0:
                return ValidationResult.reject(
                    f"Insufficient cash (need {total_cost:.0f}, have {portfolio.cash:.0f})"
                )

            logger.debug(
                "Cash check: reduced qty %d -> %d due to fees (cost=%.0f, fee=%.0f)",
                quantity,
                max_qty,
                float(order_value),
                float(estimated_fee),
            )
            return ValidationResult.accept(max_qty)

        return ValidationResult.accept(quantity)

    def _check_daily_loss_limit(self, portfolio: Portfolio) -> ValidationResult:
        """Check if daily loss limit has been exceeded."""
        if portfolio.daily_pnl < -Decimal(str(self._config.daily_loss_limit)):
            return ValidationResult.reject("Daily loss limit exceeded")
        return ValidationResult.accept(999999)

    def _max_buyable_quantity(
        self,
        current_price: Decimal | None,
        portfolio: Portfolio,
    ) -> int:
        """
        Calculate maximum quantity that can be bought including transaction costs.

        Accounts for transaction_cost_rate in the calculation.
        """
        if current_price is None or current_price <= 0:
            return 0

        # Account for transaction cost in max buyable calculation
        # total = price * qty * (1 + rate)
        # qty = cash / (price * (1 + rate))
        rate = Decimal(str(self._config.transaction_cost_rate))
        effective_price = current_price * (Decimal("1") + rate)
        return int(portfolio.cash / effective_price)
