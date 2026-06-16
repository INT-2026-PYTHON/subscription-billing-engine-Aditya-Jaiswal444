"""
DunningProcess — finite state machine for failed-payment retries.

States:
    PENDING       (initial)  →  RETRYING  on first failure
    RETRYING      ──→ SUCCEEDED    when a retry succeeds
                  ──→ FAILED_FINAL after 3 total failures
    SUCCEEDED     (terminal)
    FAILED_FINAL  (terminal — also flips subscription to PAST_DUE)

Retry schedule:
    attempt 2 scheduled at  now + 1 day
    attempt 3 scheduled at  now + 3 days
    (no attempt 4 — after the 3rd failure we mark FAILED_FINAL)

After the subscription has been PAST_DUE for 7 days with no recovery,
the BillingCycle.run (Day 2 work) may flip it to CANCELLED — that
transition does NOT live in this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Optional

from billing_engine.db import (
    InvoiceRepository, LedgerRepository, SubscriptionRepository,
    PaymentAttemptRepository,
)
from billing_engine.models import Invoice, LedgerEntry, LedgerDirection, SubscriptionStatus
from billing_engine.payments.gateway import PaymentGateway, PaymentResult


class DunningState(str, Enum):
    PENDING = "PENDING"
    RETRYING = "RETRYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED_FINAL = "FAILED_FINAL"


@dataclass(frozen=True)
class DunningOutcome:
    state: DunningState
    attempt_no: int
    next_retry_at: Optional[datetime]


# Retry intervals (in days) after each failure, indexed by attempt_no JUST COMPLETED.
# After failure of attempt 1, schedule attempt 2 at +1 day.
# After failure of attempt 2, schedule attempt 3 at +3 days.
# After failure of attempt 3, no more retries → FAILED_FINAL.
RETRY_DELAYS_DAYS = {1: 1, 2: 3}
MAX_ATTEMPTS = 3


class DunningProcess:
    def __init__(
        self,
        gateway: PaymentGateway,
        invoice_repo: InvoiceRepository,
        ledger_repo: LedgerRepository,
        subscription_repo: SubscriptionRepository,
        attempt_repo: PaymentAttemptRepository,
    ) -> None:
        self.gateway = gateway
        self.invoice_repo = invoice_repo
        self.ledger_repo = ledger_repo
        self.subscription_repo = subscription_repo
        self.attempt_repo = attempt_repo

    def attempt(self, invoice: Invoice, customer_id: int, now: datetime) -> DunningOutcome:
        """Try once. Record the attempt. Return the resulting outcome."""
        # determine attempt number
        prev = self.attempt_repo.count_for_invoice(invoice.id)
        attempt_no = prev + 1

        # perform charge
        result = self.gateway.charge(invoice)

        next_retry_at = None
        state = None

        if result.success:
            # record success
            self.attempt_repo.add(invoice.id, attempt_no, "SUCCESS", None, None)
            # mark invoice paid
            self.invoice_repo.mark_paid(invoice.id)
            # ledger credit
            credit = LedgerEntry(id=None, invoice_id=invoice.id, customer_id=customer_id, amount=invoice.total, direction=LedgerDirection.CREDIT, reason="Payment received")
            self.ledger_repo.add(credit)
            state = DunningState.SUCCEEDED
            return DunningOutcome(state=state, attempt_no=attempt_no, next_retry_at=None)

        # failure path
        failure_reason = result.failure_reason

        # schedule next retry if under MAX_ATTEMPTS
        if attempt_no in RETRY_DELAYS_DAYS and attempt_no < MAX_ATTEMPTS:
            delay = RETRY_DELAYS_DAYS[attempt_no]
            next_retry_at = now + timedelta(days=delay)
            state = DunningState.RETRYING
            status = "FAILED"
            self.attempt_repo.add(invoice.id, attempt_no, status, failure_reason, next_retry_at)
            return DunningOutcome(state=state, attempt_no=attempt_no, next_retry_at=next_retry_at)

        # attempt_no >= MAX_ATTEMPTS → final failure
        status = "FAILED"
        self.attempt_repo.add(invoice.id, attempt_no, status, failure_reason, None)
        # mark subscription past due
        self.subscription_repo.update_status(invoice.subscription_id, SubscriptionStatus.PAST_DUE, past_due_since=now.date())
        # mark invoice failed
        self.invoice_repo.mark_failed(invoice.id)
        state = DunningState.FAILED_FINAL
        return DunningOutcome(state=state, attempt_no=attempt_no, next_retry_at=None)

    # --------------------------------------------------------
    @staticmethod
    def should_cancel(past_due_since: date, today: date, grace_days: int = 7) -> bool:
        """Helper used by BillingCycle to decide PAST_DUE → CANCELLED."""
        if past_due_since is None:
            return False
        if today < past_due_since:
            return False
        delta = (today - past_due_since).days
        return delta >= grace_days
