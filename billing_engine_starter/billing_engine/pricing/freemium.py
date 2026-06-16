"""
Freemium — first N units are free, overage delegated to another strategy.

This is a great example of COMPOSITION: Freemium HAS-A inner PricingStrategy
rather than IS-A specific kind of pricing.

Example: 1000 free API calls per month, then ₹0.50 per call (UsageBased).
"""

from billing_engine.money import Money
from billing_engine.pricing.base import PricingStrategy


class Freemium(PricingStrategy):
    """Returns 0 for quantity <= free_quota, else delegates overage to inner strategy."""

    def __init__(self, free_quota: int, overage_strategy: PricingStrategy) -> None:
        if not isinstance(free_quota, int):
            raise TypeError(f"Expected int free_quota, got {type(free_quota).__name__}")
        if free_quota < 0:
            raise ValueError("free_quota must be non-negative")
        if not isinstance(overage_strategy, PricingStrategy):
            raise TypeError(
                f"Expected PricingStrategy, got {type(overage_strategy).__name__}"
            )

        self.free_quota = free_quota
        self.overage_strategy = overage_strategy

    def calculate(self, quantity: int) -> Money:
        if not isinstance(quantity, int):
            raise TypeError(f"Expected int quantity, got {type(quantity).__name__}")
        if quantity < 0:
            raise ValueError("Quantity must be non-negative")

        overage = max(0, quantity - self.free_quota)
        if overage == 0:
            return Money.zero(self.overage_strategy.calculate(0).currency)

        return self.overage_strategy.calculate(overage)
