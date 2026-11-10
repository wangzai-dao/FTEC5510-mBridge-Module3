#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模块3 命令行入口。

用法：
    python run_engine.py --dataset <module2 dataset 目录> --strategy S0
    python run_engine.py --dataset <目录> --strategy all --out outputs
    python run_engine.py --dataset <目录> --strategy all --dry-run

输出：<out>/<run_id>/{payment_results.csv, events.csv, balances.csv,
                      fx_trades.csv, run_summary.json}
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m3engine import DatasetError, load_dataset, run_dataset, write_outputs  # noqa: E402

STRATEGIES = ("S0", "S1", "S2", "S3", "S4")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", required=True,
                        help="Module 2 生成的 dataset 目录（含 config.json）")
    parser.add_argument("--strategy", default="S1",
                        help="S0/S1/S2/S3/S4 或 all，默认 S1")
    parser.add_argument("--out", default="module3_outputs",
                        help="输出根目录，默认 module3_outputs")
    parser.add_argument("--no-verify-manifest", action="store_true",
                        help="跳过 manifest.json 的 SHA256 校验")
    parser.add_argument("--dry-run", action="store_true",
                        help="只在终端打印摘要，不写文件")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    strategies = STRATEGIES if args.strategy.lower() == "all" else (args.strategy.upper(),)
    for strategy in strategies:
        if strategy not in STRATEGIES:
            print(f"未知策略 {strategy}", file=sys.stderr)
            return 2

    try:
        dataset = load_dataset(args.dataset, verify_manifest=not args.no_verify_manifest)
    except DatasetError as exc:
        print(f"[输入错误] {exc}", file=sys.stderr)
        return 1

    print(f"dataset = {dataset.dataset_id}")
    print(f"scenario = {dataset.config.get('scenario_id')} | "
          f"seed = {dataset.config.get('seed')} | "
          f"payments = {len(dataset.payments)} | inflows = {len(dataset.inflows)}")
    for warning in dataset.warnings:
        print(f"  [warning] {warning}")
    print()

    header = f"{'strategy':<9}{'settled':>9}{'pending':>9}{'queued':>8}{'FX':>5}{'FX cost':>13}"
    print(header)
    print("-" * len(header))

    out_root = Path(args.out)
    fallback4 = fallback5 = False
    for strategy in strategies:
        result = run_dataset(dataset, strategy)
        summary = result.summary
        fallback4 = fallback4 or summary["using_fallback_module4"]
        fallback5 = fallback5 or summary["using_fallback_module5"]
        print(f"{strategy:<9}{summary['settled']:>9}{summary['pending']:>9}"
              f"{summary['queued']:>8}{summary['fx_initiated']:>5}"
              f"{float(summary['fx_cost_rmb']):>13,.2f}")
        if not args.dry_run:
            target = out_root / result.run_id
            write_outputs(result, target)
            with (target / "run_summary.json").open("w", encoding="utf-8") as handle:
                json.dump(summary, handle, indent=2, ensure_ascii=False)
            print(f"          -> {target}")

    print()
    if fallback4 or fallback5:
        print("警告：S2/S3/S4 使用 adapters.py 里的 fallback（模块4 排序 / 模块5 决策）。")
        print("      这些结果只用于联调，不能写进报告。模块4/5 交付后自动接管。")
    elif any(s in ("S2", "S3", "S4") for s in strategies):
        print("模块4/5 的真实实现已接入。")
    else:
        print("S0/S1 不依赖模块4/5，结果可直接用于报告。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
