"""
BillingCycle — finds due subscriptions, generates invoices, posts ledger DEBITs,
advances the subscription period. Must be IDEMPOTENT (safe to run twice).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Optional

from billing_engine.billing.proration import compute_proration
from billing_engine.db import (
    Database,
    CustomerRepository, PlanRepository, SubscriptionRepository,
    UsageRecordRepository, InvoiceRepository, InvoiceLineItemRepository,
    LedgerRepository,
)
from billing_engine.models import (
    Invoice,
    InvoiceLineItem,
    InvoiceStatus,
    LineItemKind,
    LedgerEntry,
    LedgerDirection,
    Plan,
    Subscription,
)
from billing_engine.models import BillingPeriod, PricingType, SubscriptionStatus
from billing_engine.billing.pipeline import build_invoice
from billing_engine.money import Money
import sqlite3


@dataclass
class BillingResult:
    invoices_created: int
    invoices_skipped_duplicate: int
    trials_activated: int


class BillingCycle:
    """Day-3 deliverable. Day-4 stretch: add `upgrade_subscription(...)`."""

    def __init__(
        self,
        db: Database,
        customer_repo: CustomerRepository,
        plan_repo: PlanRepository,
        subscription_repo: SubscriptionRepository,
        usage_repo: UsageRecordRepository,
        invoice_repo: InvoiceRepository,
        line_item_repo: InvoiceLineItemRepository,
        ledger_repo: LedgerRepository,
        strategy_factory: Callable,    # given a Plan, returns a PricingStrategy
        discount_factory: Callable,    # given a discount_id or None, returns a Discount or None
        tax_factory: Callable,         # given a Customer, returns (TaxCalculator, TaxContext)
    ) -> None:
        self.db = db
        self.customer_repo = customer_repo
        self.plan_repo = plan_repo
        self.subscription_repo = subscription_repo
        self.usage_repo = usage_repo
        self.invoice_repo = invoice_repo
        self.line_item_repo = line_item_repo
        self.ledger_repo = ledger_repo
        self.strategy_factory = strategy_factory
        self.discount_factory = discount_factory
        self.tax_factory = tax_factory

    # --------------------------------------------------------
    def run(self, as_of: date) -> BillingResult:
        """Bill all subscriptions whose current period ends on or before `as_of`."""
        invoices_created = 0
        invoices_skipped_duplicate = 0
        trials_activated = 0

        # 1) Activate trials whose trial_end <= as_of
        for sub in self.subscription_repo.list_all():
            if sub.status == SubscriptionStatus.TRIAL and sub.trial_end is not None and sub.trial_end <= as_of:
                self.subscription_repo.update_status(sub.id, SubscriptionStatus.ACTIVE)
                trials_activated += 1

        # 2) Bill due ACTIVE subscriptions
        due = self.subscription_repo.get_due_for_billing(as_of)
        for sub in due:
            # gather context
            customer = self.customer_repo.get(sub.customer_id)
            plan = self.plan_repo.get(sub.plan_id)
            strategy = self.strategy_factory(plan)
            discount = self.discount_factory(sub.discount_id)
            tax_calc, tax_context = self.tax_factory(customer)

            invoice_count = self.invoice_repo.count_for_subscription(sub.id)

            # usage quantity: for now assume 0 unless plan is USAGE (tests use FLAT)
            usage_quantity = 0
            if plan.pricing_type == PricingType.USAGE:
                # default metric name 'default'
                usage_quantity = self.usage_repo.sum_for_period(sub.id, "default", sub.current_period_start, sub.current_period_end)

            inv = build_invoice(
                subscription=sub,
                plan=plan,
                strategy=strategy,
                discount=discount,
                tax_calc=tax_calc,
                tax_context=tax_context,
                usage_quantity=usage_quantity,
                period_start=sub.current_period_start,
                period_end=sub.current_period_end,
                invoice_count_so_far=invoice_count,
            )

            # mark ISSUED
            inv.status = InvoiceStatus.ISSUED

            # persist with basic idempotency handling
            try:
                created = self.invoice_repo.add(inv)
            except sqlite3.IntegrityError:
                invoices_skipped_duplicate += 1
                continue

            invoice_id = created.id

            # add line items
            for li in inv.line_items:
                item = InvoiceLineItem(id=None, invoice_id=invoice_id, description=li.description, amount=li.amount, kind=li.kind)
                self.line_item_repo.add(item)

            # post ledger debit
            ledger_entry = LedgerEntry(id=None, invoice_id=invoice_id, customer_id=sub.customer_id, amount=inv.total, direction=LedgerDirection.DEBIT, reason="Invoice issued")
            self.ledger_repo.add(ledger_entry)

            # advance subscription period
            # compute new start/end
            start = sub.current_period_end
            if plan.billing_period == BillingPeriod.MONTHLY:
                year = start.year + (1 if start.month == 12 else 0)
                month = 1 if start.month == 12 else start.month + 1
                day = start.day
                new_end = date(year, month, day)
            else:
                new_end = date(start.year + 1, start.month, start.day)

            new_start = start
            self.subscription_repo.update_period(sub.id, new_start, new_end)

            invoices_created += 1

        return BillingResult(invoices_created=invoices_created, invoices_skipped_duplicate=invoices_skipped_duplicate, trials_activated=trials_activated)

    # --------------------------------------------------------
    def upgrade_subscription(self, subscription_id: int, new_plan_id: int, switch_date: date) -> None:
        """Mid-cycle upgrade — Day 4 stretch."""
        subscription = self.subscription_repo.get(subscription_id)
        if subscription is None:
            raise ValueError(f"Subscription {subscription_id} not found")

        old_plan = self.plan_repo.get(subscription.plan_id)
        if old_plan is None:
            raise ValueError(f"Old plan {subscription.plan_id} not found")

        new_plan = self.plan_repo.get(new_plan_id)
        if new_plan is None:
            raise ValueError(f"New plan {new_plan_id} not found")

        customer = self.customer_repo.get(subscription.customer_id)
        if customer is None:
            raise ValueError(f"Customer {subscription.customer_id} not found")

        if switch_date < subscription.current_period_start or switch_date > subscription.current_period_end:
            raise ValueError("switch_date must fall inside the current billing period")

        tax_calc, tax_context = self.tax_factory(customer)
        old_strategy = self.strategy_factory(old_plan)
        new_strategy = self.strategy_factory(new_plan)
        old_price = old_strategy.calculate(0)
        new_price = new_strategy.calculate(0)

        proration = compute_proration(
            old_plan_price=old_price,
            new_plan_price=new_price,
            period_start=subscription.current_period_start,
            period_end=subscription.current_period_end,
            switch_date=switch_date,
            tax_calc=tax_calc,
            tax_context=tax_context,
        )

        invoice_period_start = switch_date
        invoice_period_end = subscription.current_period_end
        subtotal = proration.charge_amount - proration.credit_amount
        tax_total = proration.charge_tax - proration.credit_tax
        total = subtotal + tax_total
        invoice = Invoice(
            id=None,
            subscription_id=subscription.id,
            period_start=invoice_period_start,
            period_end=invoice_period_end,
            subtotal=subtotal,
            discount_total=Money.zero(subtotal.currency),
            tax_total=tax_total,
            total=total,
            status=InvoiceStatus.ISSUED,
            issued_at=datetime.now(),
            pdf_path=None,
            line_items=[],
        )

        created_invoice = self.invoice_repo.add(invoice)
        invoice_id = created_invoice.id

        credit_line = InvoiceLineItem(
            id=None,
            invoice_id=invoice_id,
            description=f"Proration credit ({old_plan.name})",
            amount=-(proration.credit_amount + proration.credit_tax),
            kind=LineItemKind.PRORATION_CREDIT,
        )
        charge_line = InvoiceLineItem(
            id=None,
            invoice_id=invoice_id,
            description=f"Proration charge ({new_plan.name})",
            amount=proration.charge_amount + proration.charge_tax,
            kind=LineItemKind.PRORATION_CHARGE,
        )
        self.line_item_repo.add(charge_line)
        self.line_item_repo.add(credit_line)

        if total.is_negative():
            ledger_direction = LedgerDirection.CREDIT
            ledger_amount = -total
            reason = "Proration credit"
        else:
            ledger_direction = LedgerDirection.DEBIT
            ledger_amount = total
            reason = "Proration charge"

        ledger_entry = LedgerEntry(
            id=None,
            invoice_id=invoice_id,
            customer_id=subscription.customer_id,
            amount=ledger_amount,
            direction=ledger_direction,
            reason=reason,
        )
        self.ledger_repo.add(ledger_entry)

        self.subscription_repo.update_plan(subscription.id, new_plan_id)

from datetime import date

from billing_engine_starter.billing_engine.models.ledger import LedgerDirection


def upgrade_subscription(self, subscription_id: int, new_plan_id: int, switch_date: date) -> Invoice:
        """Mid-cycle upgrade — Day 4 stretch."""
        sub = self.subscription_repo.get(subscription_id)
        old_plan = self.plan_repo.get(sub.plan_id)
        new_plan = self.plan_repo.get(new_plan_id)
        customer = self.customer_repo.get(sub.customer_id)

        old_price = self.strategy_factory(old_plan).calculate(0)
        new_price = self.strategy_factory(new_plan).calculate(0)
        tax_calc, tax_context = self.tax_factory(customer)
    
        pr = compute_proration(
             old_plan_price=old_price,
             new_plan_price=new_price,
             period_start=sub.current_period_start,
             period_end=sub.current_period_end,
             switch_date=switch_date,
             tax_calc=tax_calc,
             tax_context=tax_context,
        )

        subtotal = pr.charge_amount - pr.credit_amount
        tax_total = pr.charge_tax - pr.credit_tax
        total = subtotal + tax_total
        discount_total = old_price - old_price

        invoice = self.invoice_repo.add(Invoice(
            id=None,
            subscription_id=sub.id,
            period_start=sub.current_period_start,
            period_end=sub.current_period_end,
            currency=old_price.currency,
            subtotal=subtotal,
            discount_total=discount_total,
            tax_total=tax_total,
            total=total,
            status=InvoiceStatus.ISSUED,
            issued_at=switch_date,
        ))

        self.line_item_repo.add(InvoiceLineItem(
             id=None, invoice_id=invoice.id,
             description=f"Credit for unused time on {old_plan.name}",
             amount=-pr.credit_amount,
             kind=LineItemKind.PRORATION_CREDIT,
        )) 

        self.line_item_repo.add(InvoiceLineItem(
             id=None, invoice_id=invoice.id,
             description=f"Charge for remaining time on {new_plan.name}",
             amount=pr.charge_amount,
             kind=LineItemKind.PRORATION_CHARGE,
        ))
 
        self.ledger_repo.add(LedgerEntry( 
             id=None, invoice_id=invoice.id, customer_id=customer.id, 
             amount=total, direction=LedgerDirection.DEBIT,
             reason=f"Proration for upgrade to {new_plan.name} (invoice #{invoice.id})",
        ))
        self.subscription_repo.update_plan(subscription_id, new_plan_id)  

        return invoice