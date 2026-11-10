"""读取 Module 2 dataset、校验契约、写出模块3 的三张结果表。

输入目录（Module 2 的一个 dataset）：
    payments.csv, inflows.csv, config.json, manifest.json, expected_exogenous_events.csv
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from .models import Inflow, Payment, PRIORITIES, money

PAYMENT_FIELDS = ["payment_id", "arrival_time", "amount_rmb", "priority",
                  "deadline_time", "eligible"]
INFLOW_FIELDS = ["inflow_id", "arrival_time", "amount_rmb", "notice_time"]

# 交付格式（团队规范，column order 固定）
RESULT_FIELDS = ["run_id", "strategy_id", "payment_id", "arrival_time", "amount_rmb",
                 "priority", "deadline_time", "submit_time", "settle_time", "final_status"]
EVENT_FIELDS = ["run_id", "event_seq", "time", "event_type", "payment_id", "fx_id",
                "action", "reason"]
BALANCE_FIELDS = ["run_id", "event_seq", "time", "rmb_total", "rmb_reserved",
                  "rmb_locked", "rmb_available", "hkd_total", "hkd_committed",
                  "hkd_available"]
# 模块6 计算 FX 经济成本所需的补充输出（不属于团队规定的三张表，需模块6 同意）
FX_FIELDS = ["run_id", "fx_id", "target_payment_id", "initiated_at", "completed_at",
             "topup_rmb", "hkd_principal", "hkd_fee", "spread_cost_rmb",
             "total_cost_rmb", "status", "reason"]


class DatasetError(Exception):
    """输入数据违反 Module 2 契约。"""


@dataclass
class Dataset:
    config: dict
    payments: list[Payment]
    inflows: list[Inflow]
    directory: Path | None = None
    manifest: dict | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def dataset_id(self) -> str:
        return str(self.config.get("dataset_id", "dataset"))


def _read_csv(path: Path, expected: list[str]) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != expected:
            raise DatasetError(
                f"{path.name}: 表头 {reader.fieldnames!r} 与契约 {expected!r} 不一致")
        rows = list(reader)
    if any(None in row or None in row.values() for row in rows):
        raise DatasetError(f"{path.name}: 存在列数不正确的行")
    return rows


def _to_int(value: str, label: str) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise DatasetError(f"{label}: 需要整数秒，读到 {value!r}") from None
    if number < 0:
        raise DatasetError(f"{label}: 不能为负数，读到 {value!r}")
    return number


def _validate(payments: list[Payment], inflows: list[Inflow], config: dict,
              warnings: list[str]) -> None:
    errors: list[str] = []
    horizon = int(config.get("simulation_window_sec", 0))

    seen: set[str] = set()
    for p in payments:
        if p.payment_id in seen:
            errors.append(f"payment_id 重复: {p.payment_id}")
        seen.add(p.payment_id)
        if p.amount_rmb <= 0:
            errors.append(f"{p.payment_id}: amount_rmb 必须大于 0")
        if p.priority not in PRIORITIES:
            errors.append(f"{p.payment_id}: priority 非法 {p.priority!r}")
        if p.deadline_time <= p.arrival_time:
            errors.append(f"{p.payment_id}: deadline_time 必须严格大于 arrival_time")
        if horizon and p.arrival_time >= horizon:
            warnings.append(f"{p.payment_id}: arrival_time 超出观察窗口，不会到达")

    seen_inflow: set[str] = set()
    for f in inflows:
        if f.inflow_id in seen_inflow:
            errors.append(f"inflow_id 重复: {f.inflow_id}")
        seen_inflow.add(f.inflow_id)
        if f.amount_rmb <= 0:
            errors.append(f"{f.inflow_id}: amount_rmb 必须大于 0")
        if f.notice_time is not None and not (0 <= f.notice_time <= f.arrival_time):
            errors.append(f"{f.inflow_id}: notice_time 必须满足 0 <= notice <= arrival")
        if horizon and f.arrival_time > horizon:
            warnings.append(f"{f.inflow_id}: arrival_time 晚于观察终点，不会入账")

    if horizon <= 0:
        errors.append("config.simulation_window_sec 缺失或非正")
    for key in ("initial_rmb_balance", "rmb_reserve", "initial_hkd_balance",
                "fx_rate", "fx_execution_delay_sec"):
        if key not in config:
            errors.append(f"config 缺少字段: {key}")
    if errors:
        raise DatasetError("输入数据校验失败:\n  - " + "\n  - ".join(errors))


def _verify_manifest(directory: Path, manifest: dict) -> None:
    expected = manifest.get("sha256") or {}
    mismatched = []
    for name, digest in expected.items():
        target = directory / name
        if not target.exists():
            continue
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != digest:
            mismatched.append(name)
    if mismatched:
        raise DatasetError(
            "manifest.json 的 SHA256 与实际文件不符，输入在生成后被改动过: "
            + ", ".join(mismatched))


def load_dataset(directory, verify_manifest: bool = True) -> Dataset:
    directory = Path(directory)
    if not directory.is_dir():
        raise DatasetError(f"目录不存在: {directory}")

    config_path = directory / "config.json"
    if not config_path.exists():
        raise DatasetError(f"缺少 config.json: {config_path}")
    with config_path.open(encoding="utf-8") as handle:
        config = json.load(handle)

    manifest = None
    manifest_path = directory / "manifest.json"
    if manifest_path.exists():
        with manifest_path.open(encoding="utf-8") as handle:
            manifest = json.load(handle)
        if verify_manifest:
            _verify_manifest(directory, manifest)

    warnings: list[str] = []
    raw_payments = _read_csv(directory / "payments.csv", PAYMENT_FIELDS)
    raw_inflows = _read_csv(directory / "inflows.csv", INFLOW_FIELDS)

    payments = []
    for index, row in enumerate(raw_payments, start=1):
        label = f"payments.csv 第 {index} 行"
        payments.append(Payment(
            payment_id=row["payment_id"].strip(),
            arrival_time=_to_int(row["arrival_time"], f"{label}.arrival_time"),
            amount_rmb=money(row["amount_rmb"]),
            priority=row["priority"].strip(),
            deadline_time=_to_int(row["deadline_time"], f"{label}.deadline_time"),
            eligible=row["eligible"].strip().lower() == "true",
        ))

    inflows = []
    for index, row in enumerate(raw_inflows, start=1):
        label = f"inflows.csv 第 {index} 行"
        raw_notice = (row["notice_time"] or "").strip()
        inflows.append(Inflow(
            inflow_id=row["inflow_id"].strip(),
            arrival_time=_to_int(row["arrival_time"], f"{label}.arrival_time"),
            amount_rmb=money(row["amount_rmb"]),
            notice_time=_to_int(raw_notice, f"{label}.notice_time") if raw_notice else None,
        ))

    _validate(payments, inflows, config, warnings)
    return Dataset(config=config, payments=payments, inflows=inflows,
                   directory=directory, manifest=manifest, warnings=warnings)


def _blank(value) -> str:
    return "" if value is None else str(value)


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_outputs(result, out_dir) -> list[Path]:
    """写出 payment_results.csv / events.csv / balances.csv / fx_trades.csv。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, fields, rows in (
        ("payment_results.csv", RESULT_FIELDS, result.payments),
        ("events.csv", EVENT_FIELDS, result.events),
        ("balances.csv", BALANCE_FIELDS, result.balances),
        ("fx_trades.csv", FX_FIELDS, result.fx_trades),
    ):
        target = out / name
        write_csv(target, fields, rows)
        written.append(target)
    return written
