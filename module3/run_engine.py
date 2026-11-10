#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Module 3 command-line entry point.

Usage:
    python run_engine.py --dataset <module 2 dataset directory> --strategy S0
    python run_engine.py --dataset <directory> --strategy all --out outputs
    python run_engine.py --dataset <directory> --strategy all --dry-run

Output: <out>/<run_id>/{payment_results.csv, events.csv, balances.csv,
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
                        help="Module 2 dataset directory (contains config.json)")
    parser.add_argument("--strategy", default="S1",
                        help="S0/S1/S2/S3/S4 or all (default S1)")
    parser.add_argument("--out", default="module3_outputs",
                        help="output root directory (default module3_outputs)")
    parser.add_argument("--no-verify-manifest", action="store_true",
                        help="skip the manifest.json SHA256 verification")
    parser.add_argument("--dry-run", action="store_true",
                        help="print a summary only, write no files")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    strategies = STRATEGIES if args.strategy.lower() == "all" else (args.strategy.upper(),)
    for strategy in strategies:
        if strategy not in STRATEGIES:
            print(f"unknown strategy {strategy}", file=sys.stderr)
            return 2

    try:
        dataset = load_dataset(args.dataset, verify_manifest=not args.no_verify_manifest)
    except DatasetError as exc:
        print(f"[input error] {exc}", file=sys.stderr)
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
        print("WARNING: S2/S3/S4 are using the fallbacks in adapters.py (Module 4 ranking / Module 5 decision).")
        print("         Integration only - do not report these numbers. Module 4/5 take over automatically once delivered.")
    elif any(s in ("S2", "S3", "S4") for s in strategies):
        print("Real Module 4/5 implementations are in use.")
    else:
        print("S0/S1 do not depend on Module 4/5; these results are ready for the report.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
