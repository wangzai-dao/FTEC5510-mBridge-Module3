"""FTEC5510 Group 12 — 模块3：流动性感知的 mBridge 结算引擎。

只依赖 Python 标准库。输入是 Module 2 生成的 dataset 目录
（payments.csv / inflows.csv / config.json / manifest.json）。
"""

from .adapters import (fallback_decide_funding, fallback_rank_payments,
                       load_decide_funding, load_rank_payments)
from .engine import (ENGINE_DEFAULTS, EVENT_ORDER, RunResult, Simulation,
                     resolve_engine_params, run_dataset, run_simulation)
from .io import (Dataset, DatasetError, FX_FIELDS, load_dataset, write_outputs,
                 write_csv)
from .models import (INELIGIBLE, NOT_ARRIVED, PENDING, QUEUED, SETTLED,
                     Inflow, Payment, fmt, money)

__all__ = [
    "Simulation", "RunResult", "run_dataset", "run_simulation",
    "EVENT_ORDER", "ENGINE_DEFAULTS", "resolve_engine_params",
    "Dataset", "DatasetError", "load_dataset", "write_outputs", "write_csv",
    "FX_FIELDS",
    "Payment", "Inflow", "money", "fmt",
    "NOT_ARRIVED", "QUEUED", "PENDING", "SETTLED", "INELIGIBLE",
    "load_rank_payments", "load_decide_funding",
    "fallback_rank_payments", "fallback_decide_funding",
]
