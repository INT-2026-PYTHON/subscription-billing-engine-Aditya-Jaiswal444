"""
VATCalculator — single-rate VAT (e.g. 19% in Germany).
"""

from decimal import Decimal

from billing_engine.money import Money
from billing_engine.taxes.base import TaxCalculator, TaxContext, TaxBreakdown


class VATCalculator(TaxCalculator):
    def __init__(self, rate: Decimal) -> None:
        if isinstance(rate, float):
            raise TypeError("VAT rate must be Decimal, not float")
        if not isinstance(rate, Decimal):
            raise TypeError(f"Expected Decimal rate, got {type(rate).__name__}")
        if rate < 0 or rate > 1:
            raise ValueError("VAT rate must be between 0 and 1")

        self.rate = rate

    def apply(self, taxable: Money, context: TaxContext) -> TaxBreakdown:
        vat_amount = taxable * self.rate
        percent = (self.rate * Decimal(100)).normalize()
        percent_str = f"{percent}%" if percent == percent.to_integral() else f"{percent}%"
        component_name = f"VAT {percent_str}"
        return TaxBreakdown(components=[(component_name, vat_amount)], total=vat_amount)
