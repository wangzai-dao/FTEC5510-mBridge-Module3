"""Data models and money-precision helpers (Module 3 internal).

Conventions:
  * time is integer seconds (Module 2 data_dictionary v1.0.0).
  * money is Decimal quantized to 0.01 so balances reconcile exactly.
  * priority is restricted to URGENT / HIGH / NORMAL.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")

PRIORITIES = ("URGENT", "HIGH", "NORMAL")

# Terminal payment states. Module 6 metric definitions must align with these.
NOT_ARRIVED = "NOT_ARRIVED"  # internal only: not yet arrived, never queued
QUEUED = "QUEUED"          # queued, not yet submitted
PENDING = "PENDING"        # submitted, cash locked, awaiting confirmation
SETTLED = "SETTLED"        # confirmation succeeded, settled
INELIGIBLE = "INELIGIBLE"  # eligible=false, never submitted


def ZERO() -> Decimal:
    return Decimal("0.00")


def money(value) -> Decimal:
    """Safely convert any numeric value to a 2-decimal Decimal."""
    if isinstance(value, Decimal):
        amount = value
    else:
        amount = Decimal(str(value))
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def fmt(value: Decimal) -> str:
    return f"{value:.2f}"


@dataclass
class Payment:
    payment_id: str
    arrival_time: int
    amount_rmb: Decimal
    priority: str
    deadline_time: int
    eligible: bool = True
    status: str = NOT_ARRIVED
    submit_time: int | None = None
    settle_time: int | None = None
    retry_count: int = 0

    def __post_init__(self) -> None:
        self.amount_rmb = money(self.amount_rmb)
        if not self.eligible:
            self.status = INELIGIBLE


@dataclass
class Inflow:
    inflow_id: str
    arrival_time: int
    amount_rmb: Decimal
    notice_time: int | None = None
    arrived: bool = False

    def __post_init__(self) -> None:
        self.amount_rmb = money(self.amount_rmb)

    @property
    def announced(self) -> bool:
        return self.notice_time is not None
