"""
build_invoice — PURE function that turns inputs into an Invoice dataclass.

⚠️ NO database calls here. No `datetime.now()`. No PDF. Just math.

The order is FIXED:
    1. base       = strategy.calculate(usage)
    2. discount   = discount.apply(base) if discount else 0
    3. taxable    = base - discount
    4. tax        = tax_calc.apply(taxable)
    5. total      = taxable + tax.total
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from billing_engine.money import Money
from billing_engine.models import (
    Invoice, InvoiceStatus, InvoiceLineItem, LineItemKind, Subscription, Plan,
)
from billing_engine.pricing.base import PricingStrategy
from billing_engine.discounts.base import Discount, DiscountContext
from billing_engine.taxes.base import TaxCalculator, TaxContext


def build_invoice(
    subscription: Subscription,
    plan: Plan,
    strategy: PricingStrategy,
    discount: Optional[Discount],
    tax_calc: TaxCalculator,
    tax_context: TaxContext,
    usage_quantity: int,
    period_start: date,
    period_end: date,
    invoice_count_so_far: int,
) -> Invoice:
    """Pure function. Returns an Invoice (id=None, status=DRAFT) ready to be persisted."""
    # 1) base charge
    subtotal = strategy.calculate(usage_quantity)

    # 2) discount (if any)
    if discount is not None:
        discount_amount = discount.apply(subtotal, DiscountContext(invoice_count_so_far))
    else:
        discount_amount = Money.zero(subtotal.currency)

    # 3) taxable
    taxable = subtotal - discount_amount

    # 4) tax breakdown
    tax_breakdown = tax_calc.apply(taxable, tax_context)
    tax_total = tax_breakdown.total

    # 5) total
    total = taxable + tax_total

    # 6) line items
    line_items: list[InvoiceLineItem] = []

    # base vs usage line
    if plan.pricing_type == plan.pricing_type.USAGE:
        base_kind = LineItemKind.USAGE
        desc = f"Usage charge ({usage_quantity})"
    else:
        base_kind = LineItemKind.BASE
        desc = "Base charge"

    line_items.append(
        InvoiceLineItem(id=None, invoice_id=None, description=desc, amount=subtotal, kind=base_kind)
    )

    # discount line (negative amount)
    if discount_amount.is_positive():
        line_items.append(
            InvoiceLineItem(
                id=None,
                invoice_id=None,
                description="Discount",
                amount=-discount_amount,
                kind=LineItemKind.DISCOUNT,
            )
        )

    # tax component lines
    for label, amt in tax_breakdown.components:
        # amt is a Money instance
        line_items.append(
            InvoiceLineItem(id=None, invoice_id=None, description=label, amount=amt, kind=LineItemKind.TAX)
        )

    return Invoice(
        id=None,
        subscription_id=subscription.id,
        period_start=period_start,
        period_end=period_end,
        subtotal=subtotal,
        discount_total=discount_amount,
        tax_total=tax_total,
        total=total,
        status=InvoiceStatus.DRAFT,
        issued_at=None,
        pdf_path=None,
        line_items=line_items,
    )
