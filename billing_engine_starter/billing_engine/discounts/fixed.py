"""
FixedAmountDiscount — e.g., flat ₹500 off.

CAPPING RULE: if the fixed amount exceeds the subtotal, return subtotal
(so the discounted total never goes below zero).
"""

from billing_engine.money import Money
from billing_engine.discounts.base import Discount, DiscountContext


class FixedAmountDiscount(Discount):
    def __init__(self, amount: Money) -> None:
        if not isinstance(amount, Money):
            raise TypeError(f"Expected Money, got {type(amount).__name__}")
        if amount.is_negative():
            raise ValueError("Fixed discount amount must not be negative")

        self.amount = amount

    def apply(self, subtotal: Money, context: DiscountContext) -> Money:
        if subtotal.currency != self.amount.currency:
            raise ValueError("Discount currency must match subtotal currency")

        if self.amount.amount >= subtotal.amount:
            return subtotal
        return self.amount
