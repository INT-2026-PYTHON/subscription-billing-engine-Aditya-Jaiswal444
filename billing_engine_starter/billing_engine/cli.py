"""
CLI entrypoint.

Subcommands to implement (Day 4):
    billing init                              -- create / migrate the DB
    billing customer add <name> <email> <country> [--state CODE]
    billing plan list
    billing subscribe <customer_id> <plan_id> [--trial-days N] [--discount CODE]
    billing bill run [--date YYYY-MM-DD]
    billing invoice show <invoice_id>          -- prints PLAIN TEXT invoice
    billing upgrade <subscription_id> <new_plan_id> [--date YYYY-MM-DD]   (STRETCH)
    billing demo                              -- run the scripted scenario

Use argparse with subparsers. Keep each subcommand handler in its own function.

PDF rendering is OUT OF SCOPE for the core project — `invoice show` should
print a clean PLAIN-TEXT invoice (see helper `format_invoice_text` below).
PDF generation is BONUS: see `billing_engine/pdf/renderer.py`.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta

from billing_engine.models import (
    Invoice,
    BillingPeriod,
    PricingType,
    Subscription,
    SubscriptionStatus,
)
from billing_engine.money import Money


def format_invoice_text(invoice: Invoice, customer_name: str, plan_name: str) -> str:
    """Render an invoice as a plain-text receipt. Pure function — easy to test."""
    header = [
        f"INVOICE #{invoice.id}",
        "=" * 60,
        f"Customer: {customer_name}",
        f"Plan:     {plan_name}",
        f"Period:   {invoice.period_start.isoformat()} to {invoice.period_end.isoformat()}",
        "-" * 60,
    ]

    lines = []
    for item in invoice.line_items:
        amount_text = f"{item.amount.currency} {item.amount.rounded().amount:,.2f}"
        lines.append(f"{item.description:<45}{amount_text:>15}")

    footer = [
        "-" * 60,
        f"{'TOTAL':<45}{invoice.total.currency} {invoice.total.rounded().amount:>10}",
        f"Status: {invoice.status.value}",
    ]

    return "\n".join(header + lines + footer)


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _make_db() -> tuple[object, object, object, object, object, object]:
    from billing_engine.db import (
        Database,
        CustomerRepository,
        PlanRepository,
        SubscriptionRepository,
        InvoiceRepository,
        InvoiceLineItemRepository,
        LedgerRepository,
    )

    db = Database("billing.db")
    db.init_schema()
    return (
        db,
        CustomerRepository(db),
        PlanRepository(db),
        SubscriptionRepository(db),
        InvoiceRepository(db),
        InvoiceLineItemRepository(db),
        LedgerRepository(db),
    )


def _make_plan_strategy_factory() -> object:
    from billing_engine.pricing import FlatRate
    from billing_engine.models import Plan
    import json

    def factory(plan: Plan):
        if plan.pricing_type != PricingType.FLAT:
            raise ValueError("Only FLAT pricing is supported in this CLI")
        config = json.loads(plan.config_json or "{}")
        amount = config.get("amount")
        if amount is None:
            raise ValueError("Plan pricing configuration requires an 'amount'")
        return FlatRate(Money(str(amount), plan.currency))

    return factory


def _make_discount_factory() -> object:
    from billing_engine.db import Database, DiscountRepository
    from billing_engine.discounts import PercentageDiscount, FixedAmountDiscount, FirstMonthFree
    from decimal import Decimal

    db = Database("billing.db")
    repo = DiscountRepository(db)

    def factory(discount_id: int):
        if discount_id is None:
            return None
        discount = repo.get_by_id(discount_id)
        if discount is None:
            return None
        discount_type = discount["discount_type"]
        if discount_type == "PERCENT":
            return PercentageDiscount(Decimal(discount["value"]))
        if discount_type == "FIXED":
            if discount["currency"] is None:
                raise ValueError("Fixed discount requires currency")
            return FixedAmountDiscount(Money(discount["value"], discount["currency"]))
        if discount_type == "FIRST_MONTH_FREE":
            return FirstMonthFree()
        raise ValueError(f"Unknown discount_type: {discount_type}")

    return factory


def _make_tax_factory() -> object:
    def factory(customer):
        from billing_engine.taxes import NoTax
        from billing_engine.taxes import TaxContext

        return NoTax(), TaxContext(customer_country=customer.country_code)

    return factory


def _add_month(start: date) -> date:
    import calendar

    year = start.year + (1 if start.month == 12 else 0)
    month = 1 if start.month == 12 else start.month + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def handle_init(args: argparse.Namespace) -> int:
    from billing_engine.db import Database

    db = Database("billing.db")
    db.init_schema()
    print("Initialized billing.db")
    return 0


def handle_customer_add(args: argparse.Namespace) -> int:
    from billing_engine.db import Database, CustomerRepository
    from billing_engine.models import Customer

    db = Database("billing.db")
    db.init_schema()
    repo = CustomerRepository(db)
    customer = Customer(
        id=None,
        name=args.name,
        email=args.email,
        country_code=args.country,
        state_code=args.state or "",
    )
    created = repo.add(customer)
    print(f"Created customer {created.id}: {created.name} <{created.email}>")
    return 0


def handle_plan_list(args: argparse.Namespace) -> int:
    from billing_engine.db import Database, PlanRepository

    db = Database("billing.db")
    repo = PlanRepository(db)
    plans = repo.list_all()
    if not plans:
        print("No plans found.")
        return 0
    for plan in plans:
        print(f"{plan.id}: {plan.name} ({plan.pricing_type.value}, {plan.billing_period.value}, {plan.currency})")
    return 0


def handle_subscribe(args: argparse.Namespace) -> int:
    from billing_engine.db import Database, CustomerRepository, PlanRepository, SubscriptionRepository, DiscountRepository
    from billing_engine.models import Subscription

    db = Database("billing.db")
    db.init_schema()
    customer_repo = CustomerRepository(db)
    plan_repo = PlanRepository(db)
    subscription_repo = SubscriptionRepository(db)
    discount_repo = DiscountRepository(db)

    customer = customer_repo.get(args.customer_id)
    if customer is None:
        print(f"Customer {args.customer_id} not found.", file=sys.stderr)
        return 1

    plan = plan_repo.get(args.plan_id)
    if plan is None:
        print(f"Plan {args.plan_id} not found.", file=sys.stderr)
        return 1

    discount_id = None
    if args.discount:
        discount = discount_repo.get_by_code(args.discount)
        if discount is None:
            print(f"Discount code {args.discount} not found.", file=sys.stderr)
            return 1
        discount_id = discount["id"]

    start = date.today()
    end = _add_month(start) if plan.billing_period == BillingPeriod.MONTHLY else date(start.year + 1, start.month, start.day)
    status = SubscriptionStatus.TRIAL if args.trial_days and args.trial_days > 0 else SubscriptionStatus.ACTIVE
    trial_end = date.today() if args.trial_days == 0 else date.today() + timedelta(days=args.trial_days)

    subscription = Subscription(
        id=None,
        customer_id=customer.id,
        plan_id=plan.id,
        status=status,
        current_period_start=start,
        current_period_end=end,
        trial_end=trial_end if status == SubscriptionStatus.TRIAL else None,
        discount_id=discount_id,
    )
    created = subscription_repo.add(subscription)
    print(f"Created subscription {created.id} for customer {created.customer_id} on plan {created.plan_id}")
    return 0


def handle_bill_run(args: argparse.Namespace) -> int:
    from billing_engine.db import Database, CustomerRepository, PlanRepository, SubscriptionRepository, UsageRecordRepository, InvoiceRepository, InvoiceLineItemRepository, LedgerRepository
    from billing_engine.billing.cycle import BillingCycle

    db = Database("billing.db")
    db.init_schema()
    customer_repo = CustomerRepository(db)
    plan_repo = PlanRepository(db)
    subscription_repo = SubscriptionRepository(db)
    usage_repo = UsageRecordRepository(db)
    invoice_repo = InvoiceRepository(db)
    line_item_repo = InvoiceLineItemRepository(db)
    ledger_repo = LedgerRepository(db)

    strategy_factory = _make_plan_strategy_factory()
    discount_factory = _make_discount_factory()
    tax_factory = _make_tax_factory()

    cycle = BillingCycle(
        db=db,
        customer_repo=customer_repo,
        plan_repo=plan_repo,
        subscription_repo=subscription_repo,
        usage_repo=usage_repo,
        invoice_repo=invoice_repo,
        line_item_repo=line_item_repo,
        ledger_repo=ledger_repo,
        strategy_factory=strategy_factory,
        discount_factory=discount_factory,
        tax_factory=tax_factory,
    )
    target_date = _parse_date(args.date) if args.date else date.today()
    result = cycle.run(target_date)
    print(f"Billing cycle on {target_date.isoformat()}: {result.invoices_created} invoices created, {result.trials_activated} trials activated")
    return 0


def handle_invoice_show(args: argparse.Namespace) -> int:
    from billing_engine.db import Database, InvoiceRepository, InvoiceLineItemRepository, SubscriptionRepository, CustomerRepository, PlanRepository

    db = Database("billing.db")
    invoice_repo = InvoiceRepository(db)
    line_item_repo = InvoiceLineItemRepository(db)
    subscription_repo = SubscriptionRepository(db)
    plan_repo = PlanRepository(db)
    customer_repo = CustomerRepository(db)

    invoice = invoice_repo.get(args.invoice_id)
    if invoice is None:
        print(f"Invoice {args.invoice_id} not found.", file=sys.stderr)
        return 1

    line_items = line_item_repo.list_for_invoice(invoice.id)
    invoice.line_items = line_items
    subscription = subscription_repo.get(invoice.subscription_id)
    if subscription is None:
        print("Could not resolve invoice subscription.", file=sys.stderr)
        return 1

    plan = plan_repo.get(subscription.plan_id)
    customer = customer_repo.get(subscription.customer_id)
    if plan is None or customer is None:
        print("Could not resolve invoice metadata.", file=sys.stderr)
        return 1

    print(format_invoice_text(invoice, customer.name, plan.name))
    return 0


def handle_upgrade(args: argparse.Namespace) -> int:
    from billing_engine.db import Database, CustomerRepository, PlanRepository, SubscriptionRepository, UsageRecordRepository, InvoiceRepository, InvoiceLineItemRepository, LedgerRepository
    from billing_engine.billing.cycle import BillingCycle

    db = Database("billing.db")
    db.init_schema()
    customer_repo = CustomerRepository(db)
    plan_repo = PlanRepository(db)
    subscription_repo = SubscriptionRepository(db)
    usage_repo = UsageRecordRepository(db)
    invoice_repo = InvoiceRepository(db)
    line_item_repo = InvoiceLineItemRepository(db)
    ledger_repo = LedgerRepository(db)

    strategy_factory = _make_plan_strategy_factory()
    discount_factory = _make_discount_factory()
    tax_factory = _make_tax_factory()

    cycle = BillingCycle(
        db=db,
        customer_repo=customer_repo,
        plan_repo=plan_repo,
        subscription_repo=subscription_repo,
        usage_repo=usage_repo,
        invoice_repo=invoice_repo,
        line_item_repo=line_item_repo,
        ledger_repo=ledger_repo,
        strategy_factory=strategy_factory,
        discount_factory=discount_factory,
        tax_factory=tax_factory,
    )
    switch_date = _parse_date(args.date) if args.date else date.today()
    cycle.upgrade_subscription(args.subscription_id, args.new_plan_id, switch_date)
    print(f"Upgraded subscription {args.subscription_id} to plan {args.new_plan_id} on {switch_date.isoformat()}")
    return 0


def handle_demo(args: argparse.Namespace) -> int:
    return run_demo()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="billing", description="Subscription Billing CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="initialize the database").set_defaults(func=handle_init)

    customer = sub.add_parser("customer", help="manage customers")
    customer_sub = customer.add_subparsers(dest="customer_cmd", required=True)
    customer_sub.add_parser("add", help="add a customer").add_argument("name").add_argument("email").add_argument("country").add_argument("--state", default="", help="state code").set_defaults(func=handle_customer_add)

    plan = sub.add_parser("plan", help="manage plans")
    plan_sub = plan.add_subparsers(dest="plan_cmd", required=True)
    plan_sub.add_parser("list", help="list plans").set_defaults(func=handle_plan_list)

    subscribe = sub.add_parser("subscribe", help="create a subscription")
    subscribe.add_argument("customer_id", type=int)
    subscribe.add_argument("plan_id", type=int)
    subscribe.add_argument("--trial-days", type=int, default=0)
    subscribe.add_argument("--discount", default=None)
    subscribe.set_defaults(func=handle_subscribe)

    bill = sub.add_parser("bill", help="billing commands")
    bill_sub = bill.add_subparsers(dest="bill_cmd", required=True)
    bill_run = bill_sub.add_parser("run", help="run billing")
    bill_run.add_argument("--date", default=None)
    bill_run.set_defaults(func=handle_bill_run)

    invoice = sub.add_parser("invoice", help="invoice commands")
    invoice_sub = invoice.add_subparsers(dest="invoice_cmd", required=True)
    invoice_show = invoice_sub.add_parser("show", help="show an invoice")
    invoice_show.add_argument("invoice_id", type=int)
    invoice_show.set_defaults(func=handle_invoice_show)

    upgrade = sub.add_parser("upgrade", help="upgrade a subscription")
    upgrade.add_argument("subscription_id", type=int)
    upgrade.add_argument("new_plan_id", type=int)
    upgrade.add_argument("--date", default=None)
    upgrade.set_defaults(func=handle_upgrade)

    sub.add_parser("demo", help="run the demo scenario").set_defaults(func=handle_demo)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def run_demo() -> int:
    import json
    import os
    import tempfile
    from datetime import datetime

    from billing_engine.db import Database, CustomerRepository, PlanRepository, SubscriptionRepository, UsageRecordRepository, InvoiceRepository, InvoiceLineItemRepository, LedgerRepository, PaymentAttemptRepository
    from billing_engine.billing.cycle import BillingCycle
    from billing_engine.billing.dunning import DunningProcess
    from billing_engine.models import Customer, Plan, Subscription, SubscriptionStatus
    from billing_engine.payments.gateway import ScriptedGateway, PaymentResult
    from billing_engine.pricing import FlatRate
    from billing_engine.taxes import NoTax, TaxContext

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        db = Database(path)
        db.init_schema()
        customers = CustomerRepository(db)
        plans = PlanRepository(db)
        subscriptions = SubscriptionRepository(db)
        usage = UsageRecordRepository(db)
        invoices = InvoiceRepository(db)
        line_items = InvoiceLineItemRepository(db)
        ledger = LedgerRepository(db)

        customer = customers.add(Customer(None, "Alice", "alice@x.com", "AE"))
        plan = plans.add(Plan(None, "Pro", PricingType.FLAT, BillingPeriod.MONTHLY, "INR", json.dumps({"amount": "1000"})))
        subscription = subscriptions.add(Subscription(
            None,
            customer.id,
            plan.id,
            SubscriptionStatus.ACTIVE,
            date(2026, 1, 1),
            date(2026, 2, 1),
        ))

        print(f"Created customer {customer.id} and subscription {subscription.id} for plan {plan.name}")

        cycle = BillingCycle(
            db=db,
            customer_repo=customers,
            plan_repo=plans,
            subscription_repo=subscriptions,
            usage_repo=usage,
            invoice_repo=invoices,
            line_item_repo=line_items,
            ledger_repo=ledger,
            strategy_factory=lambda plan: FlatRate(Money("1000", plan.currency)),
            discount_factory=lambda discount_id: None,
            tax_factory=lambda customer: (NoTax(), TaxContext(customer_country=customer.country_code)),
        )

        print("Running billing cycle for 2026-02-01...")
        result = cycle.run(date(2026, 2, 1))
        print(f"Billing cycle created {result.invoices_created} invoice(s), skipped {result.invoices_skipped_duplicate} duplicate(s), activated {result.trials_activated} trial(s)")

        with db.connect() as conn:
            row = conn.execute("SELECT id FROM invoices WHERE subscription_id = ?", (subscription.id,)).fetchone()
        if row is None:
            print("No invoice was created.", file=sys.stderr)
            return 1
        invoice = invoices.get(row["id"])
        print(f"Generated invoice {invoice.id} with total {invoice.total}")

        dunning = DunningProcess(
            gateway=ScriptedGateway([PaymentResult(True)]),
            invoice_repo=invoices,
            ledger_repo=ledger,
            subscription_repo=subscriptions,
            attempt_repo=PaymentAttemptRepository(db),
        )

        outcome = dunning.attempt(invoice, customer.id, datetime(2026, 2, 1, 10, 0))
        print(f"Payment attempt {outcome.attempt_no}: {outcome.state.value}")
        print(f"Invoice {invoice.id} status after payment: {invoices.get(invoice.id).status.value}")

        entries = ledger.list_for_customer(customer.id)
        debit_count = sum(1 for e in entries if e.direction.value == "DEBIT")
        credit_count = sum(1 for e in entries if e.direction.value == "CREDIT")
        print(f"Ledger entries: {len(entries)} (DEBIT {debit_count}, CREDIT {credit_count})")
        return 0
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
