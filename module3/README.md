# Module 3 — Simulation Engine and Baseline Strategies (S0/S1)

FTEC5510 Advanced Financial Infrastructure · Group 12 · mBridge HKD/RMB
liquidity-aware settlement

This module implements the discrete-event engine on top of the **data contract
already delivered by Module 2** (`integration_contract.md` v1.0.0 /
`data_dictionary.md`): it loads a dataset, runs the simulation, maintains the
cash state machine, and writes the three mandated result tables. Python standard
library only — no third-party dependencies.

---

## 1. Quick start

The repository ships a **hand-checkable fixture** in `fixtures/fixed_demo/`
(10 payments, 3 inflows), so a fresh clone runs immediately:

```bash
# Self-check on the fixed fixture
python module3/run_engine.py --dataset module3/fixtures/fixed_demo --strategy all

# End-to-end checks (cash conservation, event ordering, reproducibility)
python -m unittest discover -s module3/tests -v      # 13 tests

# Full Module 2 dataset (place the module 2 repo alongside first)
python module3/run_engine.py \
  --dataset module2_ref/FTEC5510-mBridge-Module2-Copy-main/outputs/pilot/baseline/seed_1001 \
  --strategy all --out module3_outputs
```

> Tests that need the full Module 2 dataset **skip automatically** (`SkipTest`),
> so the self-check part runs anywhere.

Output is written to `<out>/<run_id>/`:

```
run_id = <dataset_id>_<strategy>      e.g. pilot_baseline_seed_1001_S0

payment_results.csv   # the 10 columns fixed by the team specification
events.csv            # the 8 columns fixed by the team specification
balances.csv          # the 10 columns fixed by the team specification
fx_trades.csv         # supplementary: FX cost input for Module 6 (needs sign-off)
run_summary.json      # resolved configuration snapshot and counters
```

---

## 2. Directory layout

```
module3/
├── run_engine.py            command-line entry point
├── upload_to_github.py      upload helper (Git Data API)
├── m3engine/
│   ├── models.py            Payment / Inflow and Decimal money precision
│   ├── io.py                dataset loading, contract validation, writers
│   ├── engine.py            event queue, cash state machine, S0/S1
│   └── adapters.py          Module 4/5 seams and integration fallbacks
├── fixtures/
│   ├── PROVENANCE.md        origin and integrity notes for the fixture
│   └── fixed_demo/          5 files copied verbatim from Module 2 (with SHA256)
└── tests/test_module3.py    acceptance tests
```

`fixtures/fixed_demo/` is **byte-identical** to the Module 2 original. The
SHA256 values in its `manifest.json` are checked by `load_dataset()`, so editing
a single byte raises an error.

---

## 3. Implemented contract

### 3.1 Same-second event ordering

Defined as `EVENT_ORDER` in `engine.py`. **This ordering is Module 3's
interpretation and must be confirmed by Module 1/6 and recorded in
`interface_spec.md`.**

| Priority | Event | Effect |
|---|---|---|
| 0 | `PAYMENT_ARRIVAL` | New instruction arrives and enters the queue |
| 1 | `INFLOW_NOTICE` | Inflow announced; **does not add cash** |
| 2 | `INFLOW_ARRIVAL` | Inflow credited; increases `rmb_total` |
| 3 | `FX_COMPLETION` | FX settles; RMB credited |
| 4 | `PAYMENT_CONFIRMATION` | Confirmation result; deducts cash, releases the lock |
| 5 | `FUNDING_REVIEW` | Periodic re-evaluation (`funding_review_interval_sec`) |

Events of equal priority are ordered by an insertion sequence number, which
makes every run exactly reproducible.

### 3.2 Payment state machine

```
NOT_ARRIVED --arrival--> QUEUED --submit (lock)--> PENDING --confirm ok--> SETTLED
                            ^                          |
                            +---confirm failed (unlock)+

eligible=false ------------------------------------> INELIGIBLE (never submitted)
```

- Submission only **locks** funds; `rmb_total` is reduced on successful confirmation.
- A failed confirmation releases the lock and returns the payment to the queue.
- `Available RMB = max(0, rmb_total - rmb_locked - rmb_reserve)`
- `Available HKD = max(0, hkd_total - hkd_committed - hkd_liquidity_buffer)`

### 3.3 FX pricing

```
effective rate   = fx_rate × (1 - fx_spread_bps / 10000)
HKD principal    = TopUpRMB / effective rate
HKD fee          = fx_fee_fixed_hkd
HKD committed    = HKD principal + HKD fee     (committed at initiation, charged on completion)
spread cost RMB  = (HKD principal - TopUpRMB / fx_rate) × fx_rate
economic cost    = spread cost + fee × fx_rate
```

`TopUpRMB = max(0, target amount - AvailableRMB) + fx_buffer_rmb`, then clamped to
`[fx_topup_min_rmb, fx_topup_max_rmb]`; exceeding the maximum counts as failing the
transaction limit.

---

## 4. Engine-owned parameters

These are **not** in the Module 2 config; Module 3 owns them and the team must
confirm the values.

| Parameter | Default | Rationale |
|---|---|---|
| `payment_confirmation_delay_sec` | 10 | baseline confirmation delay from proposal 4.1.4; absent from the Module 2 config |
| `payment_confirmation_fail_prob` | 0.0 | no confirmation failures in the baseline |
| `observation_drain_sec` | 0 | observation endpoint = `simulation_window_sec` + drain |
| `fx_buffer_rmb` | 0.0 | baseline buffer of 0 per proposal 4.2.3 |
| `fx_counterparty_capacity_mode` | `per_trade` | interpretation, see below |

Override them either as top-level keys in `config.json` or inside an
`engine_parameters` object.

### Two interpretations that need a decision

1. **Is `fx_counterparty_capacity_rmb` a per-trade ceiling or a cumulative quota?**
   This implementation treats it as a **per-trade ceiling**, consistent with
   `fx_topup_max_rmb` being a transaction limit. Set
   `fx_counterparty_capacity_mode` to `cumulative` to switch.
2. **How should `eligible=false` be treated?**
   This implementation never submits such a payment, never reserves funds, reports
   the terminal status `INELIGIBLE`, and excludes it from the numerator of the
   completion rate. Whether it belongs in the denominator, and whether it counts
   towards the breach rate, must be agreed with Module 6.

---

## 5. Wiring Module 4 and Module 5

Module 3 depends on exactly two functions:

```python
rank_payments(queue, state, config) -> {"order": [...], "scores": {...}}
decide_funding(queue, state, known_inflows, config, mode)
    -> {"action": "WAIT"|"TOP_UP"|"NO_ACTION", "target_payment_id", "topup_rmb", "reason"}
```

- Module 4 delivers `module4_rank.py`, Module 5 delivers `module5_funding.py`.
  Drop them in `m3engine/` or anywhere on `PYTHONPATH` and the engine picks them
  up; the module name can also be set with `M3_MODULE4_MODULE` /
  `M3_MODULE5_MODULE`.
- Money inside `queue` and `state` is `Decimal`; time is `int` seconds.
- **`known_inflows` contains only announced, not-yet-credited inflows inside
  `max_lookahead_sec`** — never the complete `inflows.csv`.
- Without the real implementations the fallbacks in `adapters.py` are used;
  `run_summary.json` then sets `using_fallback_module4/5` to `true` and the CLI
  prints a warning.

---

## 6. Verification status

| Check | Result |
|---|---|
| Module 3 acceptance tests | 13/13 pass |
| Built-in fixture vs Module 2 original | byte-identical (guarded by a test) |
| `fixed_demo` exogenous event timeline | matches `expected_exogenous_events.csv` entry by entry |
| Does `P000001` settle before it arrives? | No (this test caught a real bug) |
| Is `I000001` spendable before 450 s? | No |
| RMB / HKD conservation | exact to the cent, all pilot strategies |
| 21 pilot datasets × 5 strategies | 105 runs, 0 failures |
| 210 formal datasets × 5 strategies | 1050 runs, 0 failures, about 30 s |

---

## 7. Known limitations

1. **S2/S3/S4 are not part of the Module 3 deliverable.** They currently run on
   fallbacks and their numbers must not go into the report.
2. `fx_trades.csv` is a Module 3 supplementary output that the team specification
   does not define. Module 6 must either adopt it or parse the `reason` field.
3. All event times are integer seconds, following the Module 2 contract. Sub-second
   latencies would require revisiting the `EVENT_ORDER` tie-breaking rule.
4. A balance snapshot is written for **every** event so Module 6 can integrate
   liquidity hours; this is roughly 1000 rows per run.

---

## 8. Uploading to GitHub

`upload_to_github.py` uses the Git Data API and produces **one commit** for the
whole directory.

### 8.1 Create a token

GitHub → Settings → Developer settings → **Personal access tokens → Fine-grained
tokens** → Generate new token:

- **Repository access**: `Only select repositories`, pick the target repository
- **Permissions → Repository permissions → Contents**: `Read and write`
- Use a short expiry (7 days) and revoke it once the coursework is submitted

### 8.2 Upload

```bash
export GITHUB_TOKEN=github_pat_xxx      # current shell only; never write it to a file

# see what would be uploaded (no network)
python module3/upload_to_github.py --owner <you> --repo <repo name> --dry-run

# repository already exists
python module3/upload_to_github.py --owner <you> --repo <repo name>

# create a private repository if it does not exist yet
python module3/upload_to_github.py --owner <you> --repo FTEC5510-Module3 --create
```

Files land under `module3/` in the repository; change that with `--dest`.

### 8.3 Why not the Contents API

`PUT /repos/{owner}/{repo}/contents/{path}` writes one file per call, so nine files
mean nine commits, and concurrent pushes can overwrite each other. This script uses:

```
blobs -> tree (base_tree = current tree) -> commit (parents = current commit) -> PATCH ref
```

The benefit is **atomicity**: if any step fails the remote branch does not move, and
the history stays clean. `base_tree` is essential — omit it and the new tree replaces
everything already in the repository.
