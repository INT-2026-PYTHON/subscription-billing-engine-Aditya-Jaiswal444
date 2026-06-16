"""
TieredPricing — different price per unit depending on the tier the quantity falls into.

This is the "cumulative" / "stacked" tier model, NOT the "volume" model:
    Tiers: [(0, 1000, ₹2.00), (1000, 5000, ₹1.50), (5000, None, ₹1.00)]
    Quantity = 6000:
        First 1000 units  @ ₹2.00 = ₹2000
        Next  4000 units  @ ₹1.50 = ₹6000
        Last  1000 units  @ ₹1.00 = ₹1000
        ------------------------------------
        Total                     = ₹9000

A tier with `to_units = None` is the open-ended top tier.

Tier boundaries are HALF-OPEN on the right: a tier (from, to, price)
covers units strictly less than `to` (i.e. [from, to)).
"""

from dataclasses import dataclass
from typing import Optional

from billing_engine.money import Money
from billing_engine.pricing.base import PricingStrategy


@dataclass(frozen=True)
class Tier:
    from_units: int
    to_units: Optional[int]   # None means "unlimited" / open-ended
    unit_price: Money


class TieredPricing(PricingStrategy):
    """Charges across multiple price tiers based on cumulative quantity."""

    def __init__(self, tiers: list[Tier]) -> None:
        if not tiers:
            raise ValueError("TieredPricing requires at least one tier")

        currency = tiers[0].unit_price.currency
        if tiers[0].from_units != 0:
            raise ValueError("Tiers must start at 0")

        previous_to = 0
        for index, tier in enumerate(tiers):
            if tier.from_units < 0:
                raise ValueError("Tier from_units must be non-negative")
            if tier.from_units != previous_to:
                raise ValueError("Tiers must be contiguous and non-overlapping")
            if tier.to_units is not None and tier.to_units <= tier.from_units:
                raise ValueError("Tier to_units must be greater than from_units")
            if tier.unit_price.currency != currency:
                raise ValueError("All tiers must use the same currency")
            if tier.unit_price.is_negative():
                raise ValueError("Tier price must not be negative")

            previous_to = tier.to_units if tier.to_units is not None else previous_to

        if tiers[-1].to_units is not None:
            raise ValueError("The top tier must be open-ended")

        self.tiers = tiers
        self.currency = currency

    def calculate(self, quantity: int) -> Money:
        if not isinstance(quantity, int):
            raise TypeError(f"Expected int quantity, got {type(quantity).__name__}")
        if quantity < 0:
            raise ValueError("Quantity must be non-negative")

        total = Money.zero(self.currency)
        for tier in self.tiers:
            if tier.to_units is None:
                applicable = max(0, quantity - tier.from_units)
            else:
                applicable = max(0, min(quantity, tier.to_units) - tier.from_units)

            if applicable > 0:
                total += tier.unit_price * applicable

            if tier.to_units is not None and quantity < tier.to_units:
                break

        return total
