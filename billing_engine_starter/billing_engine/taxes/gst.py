"""
GSTCalculator — Indian Goods & Services Tax.

The rule:
    - If customer_state == seller_state (or seller_state is "")  =>  intra-state
        -> charge CGST + SGST (split equally, e.g. 9% + 9% = 18%)
    - Else  =>  inter-state
        -> charge IGST (e.g. 18%)

Customers without a state code default to IGST (safe choice).
"""

from decimal import Decimal

from billing_engine.money import Money
from billing_engine.taxes.base import TaxCalculator, TaxContext, TaxBreakdown


class GSTCalculator(TaxCalculator):
    def __init__(self, cgst: Decimal, sgst: Decimal, igst: Decimal) -> None:
        for rate, name in ((cgst, "cgst"), (sgst, "sgst"), (igst, "igst")):
            if isinstance(rate, float):
                raise TypeError(f"{name.upper()} rate must be Decimal, not float")
            if not isinstance(rate, Decimal):
                raise TypeError(f"Expected Decimal {name} rate, got {type(rate).__name__}")
            if rate < 0 or rate > 1:
                raise ValueError(f"{name.upper()} rate must be between 0 and 1")

        if cgst + sgst != igst:
            raise ValueError("CGST + SGST must equal IGST")

        self.cgst = cgst
        self.sgst = sgst
        self.igst = igst

    def apply(self, taxable: Money, context: TaxContext) -> TaxBreakdown:
        intra_state = bool(context.customer_state) and (
            context.customer_state == context.seller_state
        )

        if intra_state:
            cgst_amount = taxable * self.cgst
            sgst_amount = taxable * self.sgst
            components = [
                (f"CGST {(self.cgst * Decimal(100)).normalize()}%", cgst_amount),
                (f"SGST {(self.sgst * Decimal(100)).normalize()}%", sgst_amount),
            ]
            total = cgst_amount + sgst_amount
        else:
            igst_amount = taxable * self.igst
            components = [(f"IGST {(self.igst * Decimal(100)).normalize()}%", igst_amount)]
            total = igst_amount

        return TaxBreakdown(components=components, total=total)
