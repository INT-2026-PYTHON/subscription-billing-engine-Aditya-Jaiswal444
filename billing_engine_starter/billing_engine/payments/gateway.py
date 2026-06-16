"""
PaymentGateway — abstract + two mock implementations.

In real life this would talk to Stripe / Razorpay / Adyen. For the project
we use mocks so tests are deterministic and the demo never hits the network.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from billing_engine.models import Invoice


@dataclass(frozen=True)
class PaymentResult:
    success: bool
    failure_reason: Optional[str] = None


class PaymentGateway(ABC):
    @abstractmethod
    def charge(self, invoice: Invoice) -> PaymentResult:
        raise NotImplementedError


# ----------------------------------------------------------------
# Scripted — for deterministic tests
# ----------------------------------------------------------------
class ScriptedGateway(PaymentGateway):
    """Returns pre-set results from a queue. Used in tests.

    Example:
        gateway = ScriptedGateway([
            PaymentResult(False, "INSUFFICIENT_FUNDS"),
            PaymentResult(False, "INSUFFICIENT_FUNDS"),
            PaymentResult(True),
        ])
    """

    def __init__(self, results: list[PaymentResult]) -> None:
        if not isinstance(results, list):
            raise TypeError("results must be a list of PaymentResult")
        self._results = list(results)
        self._idx = 0

    def charge(self, invoice: Invoice) -> PaymentResult:
        # Return the next scripted result; if we run out, return the last result repeatedly.
        if not self._results:
            return PaymentResult(True)
        if self._idx >= len(self._results):
            return self._results[-1]
        res = self._results[self._idx]
        self._idx += 1
        return res


# ----------------------------------------------------------------
# Fake-random — for the CLI demo
# ----------------------------------------------------------------
class FakeRandomGateway(PaymentGateway):
    """Succeeds at a configurable rate; seeded for reproducibility."""

    def __init__(self, success_rate: float = 0.7, seed: Optional[int] = None) -> None:
        if not (0 <= success_rate <= 1):
            raise ValueError("success_rate must be between 0 and 1")
        import random
        self._rand = random.Random(seed)
        self._success_rate = success_rate

    def charge(self, invoice: Invoice) -> PaymentResult:
        ok = self._rand.random() < self._success_rate
        if ok:
            return PaymentResult(True)
        # pick a generic failure reason
        reason = self._rand.choice(["INSUFFICIENT_FUNDS", "CARD_DECLINED", "EXPIRED"])
        return PaymentResult(False, reason)
