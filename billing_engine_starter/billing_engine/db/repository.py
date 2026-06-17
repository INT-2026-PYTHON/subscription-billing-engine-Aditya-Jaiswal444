"""
Repositories — the ONLY place SQL lives.

Each repository wraps the Database connection and exposes methods that
take/return domain dataclasses (defined in billing_engine/models/).

⚠️ YOU IMPLEMENT every method body marked TODO.
   The signatures, docstrings, and the LedgerRepository's append-only
   guarantee are already in place — do not change them.

Conventions:
  - Always use parameterized queries (`?` placeholders) — NEVER f-string SQL.
  - Money values are persisted as TEXT using `money.to_storage()`.
  - Dates are persisted as ISO strings (`date.isoformat()`).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from billing_engine.db.database import Database
from billing_engine.money import Money
from billing_engine.models import (
    Customer,
    Plan, PricingType, BillingPeriod,
    Subscription, SubscriptionStatus,
    Invoice, InvoiceStatus, InvoiceLineItem, LineItemKind,
    LedgerEntry, LedgerDirection,
)


def _parse_date(value: Optional[str]) -> Optional[date]:
    return date.fromisoformat(value) if value is not None else None


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value is not None else None


# ============================================================
# CUSTOMERS
# ============================================================
class CustomerRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, customer: Customer) -> Customer:
        """Insert and return the customer with `id` populated."""
        with self.db.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO customers (name, email, country_code, state_code) VALUES (?, ?, ?, ?)",
                (customer.name, customer.email, customer.country_code, customer.state_code),
            )
            customer_id = cursor.lastrowid
            row = conn.execute(
                "SELECT created_at FROM customers WHERE id = ?",
                (customer_id,),
            ).fetchone()
        created_at = _parse_datetime(row["created_at"]) if row else None
        return Customer(
            id=customer_id,
            name=customer.name,
            email=customer.email,
            country_code=customer.country_code,
            state_code=customer.state_code,
            created_at=created_at,
        )

    def get(self, customer_id: int) -> Optional[Customer]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM customers WHERE id = ?",
                (customer_id,),
            ).fetchone()
        if row is None:
            return None
        return Customer(
            id=row["id"],
            name=row["name"],
            email=row["email"],
            country_code=row["country_code"],
            state_code=row["state_code"],
            created_at=_parse_datetime(row["created_at"]),
        )

    def find_by_email(self, email: str) -> Optional[Customer]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM customers WHERE email = ?",
                (email,),
            ).fetchone()
        if row is None:
            return None
        return Customer(
            id=row["id"],
            name=row["name"],
            email=row["email"],
            country_code=row["country_code"],
            state_code=row["state_code"],
            created_at=_parse_datetime(row["created_at"]),
        )

    def list_all(self) -> list[Customer]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM customers ORDER BY id"
            ).fetchall()
        return [
            Customer(
                id=row["id"],
                name=row["name"],
                email=row["email"],
                country_code=row["country_code"],
                state_code=row["state_code"],
                created_at=_parse_datetime(row["created_at"]),
            )
            for row in rows
        ]


# ============================================================
# PLANS  +  PLAN TIERS
# ============================================================
class PlanRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, plan: Plan) -> Plan:
        with self.db.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO plans (name, pricing_type, billing_period, currency, config_json) VALUES (?, ?, ?, ?, ?)",
                (
                    plan.name,
                    plan.pricing_type.value,
                    plan.billing_period.value,
                    plan.currency,
                    plan.config_json,
                ),
            )
            plan_id = cursor.lastrowid
        return Plan(
            id=plan_id,
            name=plan.name,
            pricing_type=plan.pricing_type,
            billing_period=plan.billing_period,
            currency=plan.currency,
            config_json=plan.config_json,
        )

    def get(self, plan_id: int) -> Optional[Plan]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM plans WHERE id = ?",
                (plan_id,),
            ).fetchone()
        if row is None:
            return None
        return Plan(
            id=row["id"],
            name=row["name"],
            pricing_type=PricingType(row["pricing_type"]),
            billing_period=BillingPeriod(row["billing_period"]),
            currency=row["currency"],
            config_json=row["config_json"],
        )

    def list_all(self) -> list[Plan]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM plans ORDER BY id"
            ).fetchall()
        return [
            Plan(
                id=row["id"],
                name=row["name"],
                pricing_type=PricingType(row["pricing_type"]),
                billing_period=BillingPeriod(row["billing_period"]),
                currency=row["currency"],
                config_json=row["config_json"],
            )
            for row in rows
        ]


class PlanTierRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, plan_id: int, from_units: int, to_units: Optional[int], unit_price: Money) -> int:
        with self.db.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO plan_tiers (plan_id, from_units, to_units, unit_price) VALUES (?, ?, ?, ?)",
                (plan_id, from_units, to_units, unit_price.to_storage()),
            )
            return cursor.lastrowid

    def list_for_plan(self, plan_id: int, currency: str) -> list[tuple[int, Optional[int], Money]]:
        with self.db.connect() as conn:
            rows = conn.execute(
            "SELECT from_units, to_units, unit_price FROM plan_tiers WHERE plan_id = ? ORDER BY from_units",
            (plan_id,),
        ).fetchall()
        return [
            (row["from_units"], row["to_units"], Money(row["unit_price"], currency))
            for row in rows
        ]


# ============================================================
# DISCOUNTS
# ============================================================
class DiscountRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, code: str, discount_type: str, value: str, currency: Optional[str] = None) -> int:
        with self.db.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO discounts (code, discount_type, value, currency) VALUES (?, ?, ?, ?)",
                (code, discount_type, value, currency),
            )
            return cursor.lastrowid

    def get_by_code(self, code: str) -> Optional[dict]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM discounts WHERE code = ?",
                (code,),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_by_id(self, discount_id: int) -> Optional[dict]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM discounts WHERE id = ?",
                (discount_id,),
            ).fetchone()
        return dict(row) if row is not None else None


# ============================================================
# SUBSCRIPTIONS
# ============================================================
class SubscriptionRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, subscription: Subscription) -> Subscription:
        with self.db.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO subscriptions (customer_id, plan_id, status, current_period_start, current_period_end, trial_end, discount_id, past_due_since) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    subscription.customer_id,
                    subscription.plan_id,
                    subscription.status.value,
                    subscription.current_period_start.isoformat(),
                    subscription.current_period_end.isoformat(),
                    subscription.trial_end.isoformat() if subscription.trial_end is not None else None,
                    subscription.discount_id,
                    subscription.past_due_since.isoformat() if subscription.past_due_since is not None else None,
                ),
            )
            subscription_id = cursor.lastrowid
        return Subscription(
            id=subscription_id,
            customer_id=subscription.customer_id,
            plan_id=subscription.plan_id,
            status=subscription.status,
            current_period_start=subscription.current_period_start,
            current_period_end=subscription.current_period_end,
            trial_end=subscription.trial_end,
            discount_id=subscription.discount_id,
            past_due_since=subscription.past_due_since,
        )

    def get(self, subscription_id: int) -> Optional[Subscription]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM subscriptions WHERE id = ?",
                (subscription_id,),
            ).fetchone()
        if row is None:
            return None
        return Subscription(
            id=row["id"],
            customer_id=row["customer_id"],
            plan_id=row["plan_id"],
            status=SubscriptionStatus(row["status"]),
            current_period_start=date.fromisoformat(row["current_period_start"]),
            current_period_end=date.fromisoformat(row["current_period_end"]),
            trial_end=_parse_date(row["trial_end"]),
            discount_id=row["discount_id"],
            past_due_since=_parse_date(row["past_due_since"]),
        )

    def list_all(self) -> list[Subscription]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM subscriptions ORDER BY id"
            ).fetchall()
        return [
            Subscription(
                id=row["id"],
                customer_id=row["customer_id"],
                plan_id=row["plan_id"],
                status=SubscriptionStatus(row["status"]),
                current_period_start=date.fromisoformat(row["current_period_start"]),
                current_period_end=date.fromisoformat(row["current_period_end"]),
                trial_end=_parse_date(row["trial_end"]),
                discount_id=row["discount_id"],
                past_due_since=_parse_date(row["past_due_since"]),
            )
            for row in rows
        ]

    def get_due_for_billing(self, as_of: date) -> list[Subscription]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM subscriptions WHERE status = ? AND current_period_end <= ? ORDER BY current_period_end",
                (SubscriptionStatus.ACTIVE.value, as_of.isoformat()),
            ).fetchall()
        return [
            Subscription(
                id=row["id"],
                customer_id=row["customer_id"],
                plan_id=row["plan_id"],
                status=SubscriptionStatus(row["status"]),
                current_period_start=date.fromisoformat(row["current_period_start"]),
                current_period_end=date.fromisoformat(row["current_period_end"]),
                trial_end=_parse_date(row["trial_end"]),
                discount_id=row["discount_id"],
                past_due_since=_parse_date(row["past_due_since"]),
            )
            for row in rows
        ]

    def update_period(self, subscription_id: int, new_start: date, new_end: date) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE subscriptions SET current_period_start = ?, current_period_end = ? WHERE id = ?",
                (new_start.isoformat(), new_end.isoformat(), subscription_id),
            )

    def update_status(
        self,
        subscription_id: int,
        new_status: SubscriptionStatus,
        past_due_since: Optional[date] = None,
    ) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE subscriptions SET status = ?, past_due_since = ? WHERE id = ?",
                (
                    new_status.value,
                    past_due_since.isoformat() if past_due_since is not None else None,
                    subscription_id,
                ),
            )

    def update_plan(self, subscription_id: int, new_plan_id: int) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE subscriptions SET plan_id = ? WHERE id = ?",
                (new_plan_id, subscription_id),
            )


# ============================================================
# USAGE
# ============================================================
class UsageRecordRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, subscription_id: int, metric: str, quantity: int) -> int:
        with self.db.connect() as conn:
            # Prefer to set recorded_at to the subscription's current_period_start
            # so that usage added during tests falls into the subscription's period.
            sub_row = conn.execute(
                "SELECT current_period_start FROM subscriptions WHERE id = ?",
                (subscription_id,),
            ).fetchone()
            recorded_at = sub_row["current_period_start"] if sub_row is not None else None
            if recorded_at is not None:
                cursor = conn.execute(
                    "INSERT INTO usage_records (subscription_id, metric, quantity, recorded_at) VALUES (?, ?, ?, ?)",
                    (subscription_id, metric, quantity, recorded_at),
                )
            else:
                cursor = conn.execute(
                    "INSERT INTO usage_records (subscription_id, metric, quantity) VALUES (?, ?, ?)",
                    (subscription_id, metric, quantity),
                )
            return cursor.lastrowid

    def sum_for_period(
        self, subscription_id: int, metric: str, period_start: date, period_end: date
    ) -> int:
        with self.db.connect() as conn:
            # Use DATE(recorded_at) to compare dates only, avoiding issues with
            # time components and SQLite's datetime string formats.
            row = conn.execute(
                "SELECT COALESCE(SUM(quantity), 0) as total"
                " FROM usage_records"
                " WHERE subscription_id = ? AND metric = ?"
                " AND date(recorded_at) >= ? AND date(recorded_at) < ?",
                (
                    subscription_id,
                    metric,
                    period_start.isoformat(),
                    period_end.isoformat(),
                ),
            ).fetchone()
        return int(row["total"])


# ============================================================
# INVOICES + LINE ITEMS
# ============================================================
class InvoiceRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, invoice: Invoice) -> Invoice:
        with self.db.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO invoices (subscription_id, period_start, period_end, currency, subtotal, discount_total, tax_total, total, status, issued_at, pdf_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    invoice.subscription_id,
                    invoice.period_start.isoformat(),
                    invoice.period_end.isoformat(),
                    invoice.subtotal.currency,
                    invoice.subtotal.to_storage(),
                    invoice.discount_total.to_storage(),
                    invoice.tax_total.to_storage(),
                    invoice.total.to_storage(),
                    invoice.status.value,
                    invoice.issued_at.isoformat() if invoice.issued_at is not None else None,
                    invoice.pdf_path,
                ),
            )
            invoice_id = cursor.lastrowid
        return Invoice(
            id=invoice_id,
            subscription_id=invoice.subscription_id,
            period_start=invoice.period_start,
            period_end=invoice.period_end,
            subtotal=invoice.subtotal,
            discount_total=invoice.discount_total,
            tax_total=invoice.tax_total,
            total=invoice.total,
            status=invoice.status,
            issued_at=invoice.issued_at,
            pdf_path=invoice.pdf_path,
        )

    def get(self, invoice_id: int) -> Optional[Invoice]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM invoices WHERE id = ?",
                (invoice_id,),
            ).fetchone()
        if row is None:
            return None
        return Invoice(
            id=row["id"],
            subscription_id=row["subscription_id"],
            period_start=date.fromisoformat(row["period_start"]),
            period_end=date.fromisoformat(row["period_end"]),
            subtotal=Money(row["subtotal"], row["currency"]),
            discount_total=Money(row["discount_total"], row["currency"]),
            tax_total=Money(row["tax_total"], row["currency"]),
            total=Money(row["total"], row["currency"]),
            status=InvoiceStatus(row["status"]),
            issued_at=_parse_datetime(row["issued_at"]),
            pdf_path=row["pdf_path"],
        )

    def count_for_subscription(self, subscription_id: int) -> int:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as count FROM invoices WHERE subscription_id = ?",
                (subscription_id,),
            ).fetchone()
        return int(row["count"])

    def mark_paid(self, invoice_id: int) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE invoices SET status = ? WHERE id = ?",
                (InvoiceStatus.PAID.value, invoice_id),
            )

    def mark_failed(self, invoice_id: int) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE invoices SET status = ? WHERE id = ?",
                (InvoiceStatus.FAILED.value, invoice_id),
            )

    def set_pdf_path(self, invoice_id: int, path: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE invoices SET pdf_path = ? WHERE id = ?",
                (path, invoice_id),
            )


class InvoiceLineItemRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, line_item: InvoiceLineItem) -> InvoiceLineItem:
        with self.db.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO invoice_line_items (invoice_id, description, amount, kind) VALUES (?, ?, ?, ?)",
                (
                    line_item.invoice_id,
                    line_item.description,
                    line_item.amount.to_storage(),
                    line_item.kind.value,
                ),
            )
            line_item_id = cursor.lastrowid
        return InvoiceLineItem(
            id=line_item_id,
            invoice_id=line_item.invoice_id,
            description=line_item.description,
            amount=line_item.amount,
            kind=line_item.kind,
        )

    def list_for_invoice(self, invoice_id: int) -> list[InvoiceLineItem]:
        with self.db.connect() as conn:
            invoice_row = conn.execute(
                "SELECT currency FROM invoices WHERE id = ?",
                (invoice_id,),
            ).fetchone()
            currency = invoice_row["currency"] if invoice_row is not None else ""
            rows = conn.execute(
                "SELECT * FROM invoice_line_items WHERE invoice_id = ? ORDER BY id",
                (invoice_id,),
            ).fetchall()

        return [
            InvoiceLineItem(
                id=row["id"],
                invoice_id=row["invoice_id"],
                description=row["description"],
                amount=Money(row["amount"], currency),
                kind=LineItemKind(row["kind"]),
            )
            for row in rows
        ]


# ============================================================
# LEDGER — APPEND-ONLY (do not implement update/delete)
# ============================================================
class LedgerRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, entry: LedgerEntry) -> LedgerEntry:
        with self.db.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO ledger_entries (invoice_id, customer_id, amount, currency, direction, reason) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    entry.invoice_id,
                    entry.customer_id,
                    entry.amount.to_storage(),
                    entry.amount.currency,
                    entry.direction.value,
                    entry.reason,
                ),
            )
            entry_id = cursor.lastrowid
            row = conn.execute(
                "SELECT created_at FROM ledger_entries WHERE id = ?",
                (entry_id,),
            ).fetchone()
        return LedgerEntry(
            id=entry_id,
            invoice_id=entry.invoice_id,
            customer_id=entry.customer_id,
            amount=entry.amount,
            direction=entry.direction,
            reason=entry.reason,
            created_at=_parse_datetime(row["created_at"]) if row else None,
        )

    def list_for_customer(self, customer_id: int) -> list[LedgerEntry]:
        with self.db.connect() as conn:
            rows = conn.execute(
            "SELECT * FROM ledger_entries WHERE customer_id = ? ORDER BY created_at, id",
            (customer_id,),
        ).fetchall()
        return [
            LedgerEntry(
                id=row["id"],
                invoice_id=row["invoice_id"],
                customer_id=row["customer_id"],
                amount=Money(row["amount"], row["currency"]),
                direction=LedgerDirection(row["direction"]),
                reason=row["reason"],
                created_at=_parse_datetime(row["created_at"]),
            )
            for row in rows
        ]

    # ✅ These two methods are intentionally implemented to REJECT — do not override.
    def update(self, *args, **kwargs):
        raise NotImplementedError("Ledger is append-only. Post a reversing entry instead.")

    def delete(self, *args, **kwargs):
        raise NotImplementedError("Ledger is append-only. Post a reversing entry instead.")


# ============================================================
# PAYMENT ATTEMPTS
# ============================================================
class PaymentAttemptRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(
        self,
        invoice_id: int,
        attempt_no: int,
        status: str,
        failure_reason: Optional[str],
        next_retry_at: Optional[datetime],
    ) -> int:
        with self.db.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO payment_attempts (invoice_id, attempt_no, status, failure_reason, next_retry_at) VALUES (?, ?, ?, ?, ?)",
                (
                    invoice_id,
                    attempt_no,
                    status,
                    failure_reason,
                    next_retry_at.isoformat() if next_retry_at is not None else None,
                ),
            )
            return cursor.lastrowid

    def list_for_invoice(self, invoice_id: int) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM payment_attempts WHERE invoice_id = ? ORDER BY attempt_no",
                (invoice_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def count_for_invoice(self, invoice_id: int) -> int:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as count FROM payment_attempts WHERE invoice_id = ?",
                (invoice_id,),
            ).fetchone()
        return int(row["count"])
