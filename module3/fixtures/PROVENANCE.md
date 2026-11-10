# Fixture provenance

The five files under `fixed_demo/` are **copied verbatim** from the Module 2
repository; nothing was modified.

| Item | Value |
|---|---|
| Source repository | `FTEC5510-mBridge-Module2-Copy` (private) |
| Path inside that repo | `examples/fixed_demo/` |
| Files | `config.json`, `payments.csv`, `inflows.csv`, `expected_exogenous_events.csv`, `manifest.json` |
| `dataset_id` | `fixed_demo_v1` |
| `schema_version` | `1.0.0` |
| Scale | 10 payments, 3 inflows, 7200-second observation window |

## Why it is copied here

The Module 3 acceptance tests need a **hand-checkable** input. Shipping the fixture
in this repository means:

1. a fresh clone can run `python -m unittest` without the Module 2 repository;
2. the fixture version travels with the code, so the two cannot drift apart.

## Integrity constraint

`manifest.json` records the SHA256 of the other four files.
`m3engine.io.load_dataset()` verifies them by default and fails immediately if any
file has been altered.

**Do not edit anything in this directory by hand.** To refresh the fixture, copy it
again from Module 2 and confirm that

```bash
python module3/run_engine.py --dataset module3/fixtures/fixed_demo --strategy S1 --dry-run
```

still succeeds.

## This is input, not results

The fixture contains exogenous events only (payment arrivals, inflow notices,
inflow credits) and **no settlement outcome**. Settlement results are produced at
runtime:

```bash
python module3/run_engine.py --dataset module3/fixtures/fixed_demo --strategy all --out module3_outputs
```
