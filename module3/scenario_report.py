#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Print a compact scenario-by-strategy coverage table for a pilot dataset set.

This is a **smoke report**, not the official performance metric set: it exists so
the CI summary shows that all seven scenarios were actually executed and which of
them bind. Module 6 owns the metric definitions.

Usage:
    python module3/scenario_report.py --root module3/fixtures/pilot
    python module3/scenario_report.py --root module3/fixtures/pilot --markdown
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from m3engine import load_dataset, run_dataset  # noqa: E402

STRATEGIES = ("S0", "S1", "S2", "S3", "S4")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default="module3/fixtures/pilot",
                        help="directory containing <scenario>/seed_<n>/ datasets")
    parser.add_argument("--strategy", default="all", help="S0..S4 or all")
    parser.add_argument("--markdown", action="store_true",
                        help="emit a Markdown table (for $GITHUB_STEP_SUMMARY)")
    args = parser.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        raise SystemExit(f"Directory not found: {root}")
    datasets = sorted(root.glob("*/seed_*"))
    if not datasets:
        raise SystemExit(f"No datasets under {root}")

    strategies = STRATEGIES if args.strategy == "all" else (args.strategy.upper(),)

    settled = {}          # (scenario, strategy) -> settled count summed over seeds
    fx = {}               # (scenario, strategy) -> FX trades summed over seeds
    per_dataset = {}      # scenario -> payments per dataset
    seed_count = {}       # scenario -> number of datasets
    runs = 0

    for directory in datasets:
        dataset = load_dataset(directory)
        scenario = str(dataset.config["scenario_id"])
        per_dataset[scenario] = len(dataset.payments)
        seed_count[scenario] = seed_count.get(scenario, 0) + 1
        for strategy in strategies:
            summary = run_dataset(dataset, strategy).summary
            key = (scenario, strategy)
            settled[key] = settled.get(key, 0) + summary["settled"]
            fx[key] = fx.get(key, 0) + summary["fx_initiated"]
            runs += 1

    scenarios = sorted({scenario for scenario, _ in settled})
    header = ["scenario", "payments/run"] + [f"{s} settled" for s in strategies]

    body = [[s, str(per_dataset[s])]
            + [f"{settled[(s, st)] / seed_count[s]:.0f}" for st in strategies]
            for s in scenarios]

    if args.markdown:
        print(f"### Scenario coverage - {len(datasets)} datasets, {runs} runs")
        print()
        print("| " + " | ".join(header) + " |")
        print("|" + "---|" * len(header))
        for line in body:
            print("| " + " | ".join(line) + " |")
        print()
        print("Mean FX trades per run:")
        print()
        print("| " + " | ".join(["scenario"] + list(strategies)) + " |")
        print("|" + "---|" * (len(strategies) + 1))
        for scenario in scenarios:
            cells = [f"{fx[(scenario, st)] / seed_count[scenario]:.1f}" for st in strategies]
            print("| " + " | ".join([scenario] + cells) + " |")
    else:
        print(f"{len(datasets)} datasets, {runs} runs")
        print()
        print("".join(f"{h:<20}" for h in header))
        for line in body:
            print("".join(f"{value:<20}" for value in line))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
