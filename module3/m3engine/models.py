"""数据模型与货币精度工具（模块3 内部使用）。

约定：
  * 时间一律为整数秒（Module 2 data_dictionary v1.0.0）。
  * 金额一律为 Decimal，量化到 0.01，避免浮点误差导致余额对不上。
  * priority 只允许 URGENT / HIGH / NORMAL。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")

PRIORITIES = ("URGENT", "HIGH", "NORMAL")

# 付款最终状态。Module 6 的指标口径需要与此一致。
NOT_ARRIVED = "NOT_ARRIVED"  # 引擎内部状态：尚未到达，不进入队列
QUEUED = "QUEUED"          # 在队列中排队，未提交
PENDING = "PENDING"        # 已提交、资金已锁定，等待确认
SETTLED = "SETTLED"        # 确认成功，已结算
INELIGIBLE = "INELIGIBLE"  # eligible=false，引擎不提交


def ZERO() -> Decimal:
    return Decimal("0.00")


def money(value) -> Decimal:
    """把任意数值安全转换为两位小数的 Decimal。"""
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
