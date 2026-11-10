#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Acceptance tests for Module 3.

Items 1-5 mirror the "Smoke test to request from Module 3" section of
module2's integration_contract.md; the remaining tests cover Module 3's own conservation and reproducibility checks.

Run: python3 -m unittest discover -s module3/tests -v
"""

import csv
import json
import shutil
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "module3"))

from m3engine import DatasetError, load_dataset, run_dataset, write_outputs  # noqa: E402

MODULE2 = ROOT / "module2_ref" / "FTEC5510-mBridge-Module2-Copy-main"
FIXED_DEMO_SOURCE = MODULE2 / "examples" / "fixed_demo"
# prefer the built-in fixture so a fresh clone runs without the module2 repo
FIXTURE = ROOT / "module3" / "fixtures" / "fixed_demo"
FIXED_DEMO = FIXTURE if FIXTURE.exists() else FIXED_DEMO_SOURCE
PILOT_SEED_1001 = MODULE2 / "outputs" / "pilot" / "baseline" / "seed_1001"
STRATEGIES = ("S0", "S1", "S2", "S3", "S4")

EXOGENOUS = {"PAYMENT_ARRIVAL", "INFLOW_NOTICE", "INFLOW_ARRIVAL"}


def _require(path: Path) -> None:
    if not path.exists():
        raise unittest.SkipTest(f"Module 2 data not found: {path}")


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _settled_total(result) -> Decimal:
    return sum((Decimal(row["amount_rmb"]) for row in result.payments
                if row["final_status"] == "SETTLED"), Decimal("0"))


def _arrived_inflow_total(dataset) -> Decimal:
    horizon = int(dataset.config["simulation_window_sec"])
    return sum((f.amount_rmb for f in dataset.inflows if f.arrival_time <= horizon),
               Decimal("0"))


def _fx_total(result) -> Decimal:
    return sum((Decimal(row["topup_rmb"]) for row in result.fx_trades
                if row["status"] == "COMPLETED"), Decimal("0"))


class FixedDemoContractTests(unittest.TestCase):
    """Items 1-4 of integration_contract.md."""

    @classmethod
    def setUpClass(cls):
        _require(FIXED_DEMO)
        cls.dataset = load_dataset(FIXED_DEMO)

    def test_1_fixed_demo_loads(self):
        self.assertEqual(len(self.dataset.payments), 10)
        self.assertEqual(len(self.dataset.inflows), 3)

    def test_2_exogenous_event_timeline_matches_expected(self):
        result = run_dataset(self.dataset, "S1")

        def reference(row):
            if row["payment_id"]:
                return row["payment_id"]
            if "inflow_id=" in row["reason"]:
                return row["reason"].split("inflow_id=")[-1].split(";")[0]
            return ""

        engine_rows = sorted(
            (int(row["time"]), row["event_type"], reference(row))
            for row in result.events if row["event_type"] in EXOGENOUS)

        expected = []
        for row in _read_csv(FIXED_DEMO / "expected_exogenous_events.csv"):
            expected.append((int(row["time"]), row["event_type"], row["reference_id"]))
        expected.sort()

        self.assertEqual(len(engine_rows), len(expected))
        self.assertEqual(engine_rows, expected)

    def test_3a_no_payment_settles_before_it_arrives(self):
        for strategy in STRATEGIES:
            result = run_dataset(self.dataset, strategy)
            for row in result.payments:
                if row["submit_time"] == "":
                    continue
                self.assertGreaterEqual(
                    int(row["submit_time"]), int(row["arrival_time"]),
                    f"{strategy}: {row['payment_id']} was submitted before it arrived")
                if row["settle_time"] != "":
                    self.assertGreaterEqual(int(row["settle_time"]),
                                            int(row["arrival_time"]))

    def test_3b_announced_inflow_is_not_cash_before_arrival(self):
        result = run_dataset(self.dataset, "S1")
        rows = [{"time": int(row["time"]), "rmb_total": Decimal(row["rmb_total"])}
                for row in result.balances]
        jumps = [(row["time"], row["rmb_total"] - rows[i - 1]["rmb_total"])
                 for i, row in enumerate(rows) if i > 0]
        first_big_credit = next((time for time, delta in jumps
                                 if delta >= Decimal("300000")), None)
        self.assertEqual(first_big_credit, 450,
                         "I000001 is announced at 210 s, but the cash must not be usable before 450 s")

    def test_4_balance_invariants_hold(self):
        for strategy in STRATEGIES:
            result = run_dataset(self.dataset, strategy)
            expected = (Decimal(str(self.dataset.config["initial_rmb_balance"]))
                        + _arrived_inflow_total(self.dataset)
                        + _fx_total(result)
                        - _settled_total(result))
            actual = Decimal(result.summary["rmb_total_end"])
            self.assertEqual(actual, expected, f"{strategy}: RMB balance is not conserved")

            for row in result.balances:
                for key in ("rmb_available", "hkd_available", "rmb_locked",
                            "hkd_committed", "rmb_total", "hkd_total"):
                    self.assertGreaterEqual(Decimal(row[key]), Decimal("0"),
                                            f"{strategy}: {key} went negative")
            self.assertEqual(Decimal(result.summary["rmb_locked_end"]), Decimal("0"))

    def test_5_all_strategies_share_inputs_and_have_distinct_run_ids(self):
        run_ids = set()
        for strategy in STRATEGIES:
            result = run_dataset(self.dataset, strategy)
            run_ids.add(result.run_id)
            self.assertEqual(len(result.payments), 10)
            self.assertEqual({row["payment_id"] for row in result.payments},
                             {p.payment_id for p in self.dataset.payments})
        self.assertEqual(len(run_ids), 5)
        self.assertEqual(run_ids, {f"fixed_demo_v1_{s}" for s in STRATEGIES})


class AccountingTests(unittest.TestCase):
    """Conservation and reproducibility checks on the real pilot dataset."""

    @classmethod
    def setUpClass(cls):
        _require(PILOT_SEED_1001)
        cls.dataset = load_dataset(PILOT_SEED_1001)

    def test_pilot_balance_conservation(self):
        for strategy in STRATEGIES:
            result = run_dataset(self.dataset, strategy)
            expected = (Decimal(str(self.dataset.config["initial_rmb_balance"]))
                        + _arrived_inflow_total(self.dataset)
                        + _fx_total(result)
                        - _settled_total(result))
            actual = Decimal(result.summary["rmb_total_end"])
            self.assertEqual(actual, expected, f"{strategy}: balance is not conserved")

    def test_no_payment_settled_twice(self):
        result = run_dataset(self.dataset, "S1")
        settled = [row["payment_id"] for row in result.events
                   if row["event_type"] == "PAYMENT_SETTLED"]
        self.assertEqual(len(settled), len(set(settled)), "a payment was settled twice")

    def test_hkd_conservation(self):
        result = run_dataset(self.dataset, "S1")
        spent = sum((Decimal(row["hkd_principal"]) + Decimal(row["hkd_fee"])
                     for row in result.fx_trades if row["status"] == "COMPLETED"),
                    Decimal("0"))
        expected = Decimal(str(self.dataset.config["initial_hkd_balance"])) - spent
        self.assertEqual(Decimal(result.summary["hkd_total_end"]), expected)

    def test_deterministic_runs(self):
        first = run_dataset(self.dataset, "S1")
        second = run_dataset(self.dataset, "S1")
        self.assertEqual(first.payments, second.payments)
        self.assertEqual(first.events, second.events)
        self.assertEqual(first.balances, second.balances)


class ManifestAndOutputTests(unittest.TestCase):
    def test_fixture_is_byte_identical_to_module2_source(self):
        """The built-in fixture must stay byte-identical to the module 2 original."""
        if not FIXED_DEMO_SOURCE.exists():
            raise unittest.SkipTest("no module 2 repo on this machine; skipping the comparison")
        for name in ("config.json", "payments.csv", "inflows.csv",
                     "expected_exogenous_events.csv", "manifest.json"):
            self.assertEqual((FIXTURE / name).read_bytes(),
                             (FIXED_DEMO_SOURCE / name).read_bytes(),
                             f"{name} differs from the module 2 original")

    def test_manifest_tampering_is_detected(self):
        _require(FIXED_DEMO)
        with tempfile.TemporaryDirectory() as tmp:
            copy_dir = Path(tmp) / "fixed_demo"
            shutil.copytree(FIXED_DEMO, copy_dir)
            with (copy_dir / "inflows.csv").open("a", encoding="utf-8") as handle:
                handle.write("\n")
            with self.assertRaises(DatasetError):
                load_dataset(copy_dir)

    def test_outputs_written_with_exact_headers(self):
        _require(FIXED_DEMO)
        dataset = load_dataset(FIXED_DEMO)
        result = run_dataset(dataset, "S1")
        with tempfile.TemporaryDirectory() as tmp:
            write_outputs(result, tmp)
            self.assertEqual(_header(Path(tmp) / "payment_results.csv"),
                             ["run_id", "strategy_id", "payment_id", "arrival_time",
                              "amount_rmb", "priority", "deadline_time", "submit_time",
                              "settle_time", "final_status"])
            self.assertEqual(_header(Path(tmp) / "events.csv"),
                             ["run_id", "event_seq", "time", "event_type",
                              "payment_id", "fx_id", "action", "reason"])
            self.assertEqual(_header(Path(tmp) / "balances.csv"),
                             ["run_id", "event_seq", "time", "rmb_total", "rmb_reserved",
                              "rmb_locked", "rmb_available", "hkd_total",
                              "hkd_committed", "hkd_available"])
            rows = _read_csv(Path(tmp) / "payment_results.csv")
            self.assertTrue(all(row["run_id"] == "fixed_demo_v1_S1" for row in rows))
            # timestamps that did not happen must be blank, never 0
            for row in rows:
                if row["final_status"] == "QUEUED":
                    self.assertEqual(row["submit_time"], "")


def _header(path: Path) -> list[str]:
    with path.open(encoding="utf-8", newline="") as handle:
        return next(csv.reader(handle))


if __name__ == "__main__":
    unittest.main(verbosity=2)
