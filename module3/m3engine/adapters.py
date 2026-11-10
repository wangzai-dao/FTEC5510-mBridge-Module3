"""与模块4（动态排序）、模块5（等待/换汇决策）的接线口。

模块3 只认两个函数，签名来自 integration_contract.md：

    rank_payments(queue, state, config) -> {"order": [payment_id, ...], "scores": {...}}
    decide_funding(queue, state, known_inflows, config, mode)
        -> {"action": "WAIT"|"TOP_UP"|"NO_ACTION", "target_payment_id", "topup_rmb", "reason"}

模块4/5 只要各自提供 `module4_rank.py` / `module5_funding.py`（放在本目录或
PYTHONPATH 上），引擎会自动加载；也可以用环境变量指定模块名：

    M3_MODULE4_MODULE=my_rank_module
    M3_MODULE5_MODULE=my_funding_module

拿不到真实实现时使用下列 fallback。fallback 的输出**不代表模块4/5 的最终结论**，
只用于让 S2–S4 能跑通集成流程，run_summary.json 里会标注 using_fallback=true。
"""

from __future__ import annotations

import importlib
import os
from decimal import Decimal

from .models import ZERO, money


# --------------------------------------------------------------------------
# 模块4：排序
# --------------------------------------------------------------------------

def fallback_rank_payments(queue: list[dict], state: dict, config: dict) -> dict:
    """占位实现：按 config.ranking_parameters 打分，仅用于联调。

    模块4 交付后应完全替换本函数。
    """
    params = config.get("ranking_parameters", {})
    points = params.get("priority_points", {"URGENT": 3.0, "HIGH": 2.0, "NORMAL": 1.0})
    w_priority = float(params.get("weight_priority", 0.45))
    w_deadline = float(params.get("weight_deadline", 0.40))
    w_aging = float(params.get("weight_aging", 0.15))
    deadline_norm = float(params.get("deadline_normalizer_sec", 7200)) or 1.0
    aging_norm = float(params.get("aging_normalizer_sec", 7200)) or 1.0

    now = int(state["t"])
    max_points = max(points.values())
    scored = []
    for payment in queue:
        slack = max(0, int(payment["deadline_time"]) - now)
        deadline_score = 1.0 - min(1.0, slack / deadline_norm)
        aging_score = min(1.0, (now - int(payment["arrival_time"])) / aging_norm)
        priority_score = points[payment["priority"]] / max_points
        score = (w_priority * priority_score
                 + w_deadline * deadline_score
                 + w_aging * aging_score)
        scored.append((-score, int(payment["arrival_time"]),
                       str(payment["payment_id"]), score))

    scored.sort()
    return {
        "order": [row[2] for row in scored],
        "scores": {row[2]: {"score": round(row[3], 6)} for row in scored},
    }


def load_rank_payments():
    """返回 (函数, 是否使用了 fallback)。"""
    name = os.environ.get("M3_MODULE4_MODULE", "module4_rank")
    try:
        module = importlib.import_module(name)
        function = getattr(module, "rank_payments")
        return function, False
    except (ImportError, AttributeError):
        return fallback_rank_payments, True


# --------------------------------------------------------------------------
# 模块5：等待 / 换汇决策
# --------------------------------------------------------------------------

def fallback_decide_funding(queue: list[dict], state: dict, known_inflows: list[dict],
                            config: dict, mode: str) -> dict:
    """占位实现：永远立即换汇。仅用于联调，模块5 交付后应完全替换。"""
    available = money(state["rmb_available"])
    for payment in queue:
        gap = payment["amount_rmb"] - available
        if gap > ZERO():
            return {
                "action": "TOP_UP",
                "target_payment_id": payment["payment_id"],
                "topup_rmb": gap,
                "reason": "fallback_immediate_topup",
                "next_check_time": None,
            }
    return {"action": "NO_ACTION", "target_payment_id": None, "topup_rmb": ZERO(),
            "reason": "fallback_no_gap", "next_check_time": None}


def load_decide_funding():
    """返回 (函数, 是否使用了 fallback)。"""
    name = os.environ.get("M3_MODULE5_MODULE", "module5_funding")
    try:
        module = importlib.import_module(name)
        function = getattr(module, "decide_funding")
        return function, False
    except (ImportError, AttributeError):
        return fallback_decide_funding, True
