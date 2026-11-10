# 模块3 — 仿真引擎与基础策略（S0/S1）

FTEC5510 Group 12 · mBridge HKD/RMB 流动性感知结算

本目录按 **Module 2 已交付的数据契约**（`integration_contract.md` v1.0.0 /
`data_dictionary.md`）实现模块3：读取 dataset、跑离散事件仿真、维护资金状态机、
输出三张规定的结果表。只依赖 Python 标准库。

---

## 1. 快速开始

仓库**自带固定样例** `fixtures/fixed_demo/`（10 笔付款 / 3 笔入账），clone 下来直接能跑：

```bash
# 自检：固定样例，可逐笔核对
python module3/run_engine.py --dataset module3/fixtures/fixed_demo --strategy all

# 换汇与余额守恒的端到端验证
python -m unittest discover -s module3/tests -v      # 13 个测试

# 用完整的 module2 数据集（需先把 module2 仓库放在同级）
python module3/run_engine.py \
  --dataset module2_ref/FTEC5510-mBridge-Module2-Copy-main/outputs/pilot/baseline/seed_1001 \
  --strategy all --out module3_outputs
```

> `tests/` 里依赖 module2 全量数据的用例会**自动跳过**（`SkipTest`），所以没有
> module2 仓库也能完整跑通自检部分。

产物写到 `<out>/<run_id>/`：

```
run_id = <dataset_id>_<strategy>      例如 pilot_baseline_seed_1001_S0

payment_results.csv   # 契约规定的 10 列
events.csv            # 契约规定的 8 列
balances.csv          # 契约规定的 10 列
fx_trades.csv         # 补充：模块6 算 FX 经济成本用（需模块6 同意）
run_summary.json      # 本次运行的配置快照与计数
```

---

## 2. 目录结构

```
module3/
├── run_engine.py            命令行入口
├── upload_to_github.py      上传工具（Git Data API）
├── m3engine/
│   ├── models.py            Payment / Inflow / 金额精度（Decimal 到分）
│   ├── io.py                读 dataset、校验契约、写结果表
│   ├── engine.py            事件队列、资金状态机、S0/S1
│   └── adapters.py          模块4/5 接线口 + 联调用 fallback
├── fixtures/
│   ├── PROVENANCE.md        固定样例的来源与完整性说明
│   └── fixed_demo/          module2 原样复制的 5 个文件（含 manifest SHA256）
└── tests/test_module3.py    验收测试
```

`fixtures/fixed_demo/` 与 Module 2 原件**逐字节一致**，`manifest.json` 里的
SHA256 会在 `load_dataset()` 时自动校验，改动任何一个字节都会报错。

---

## 3. 已实现的契约

### 3.1 同一秒内的事件顺序

`engine.py` 的 `EVENT_ORDER`，**这个顺序由模块3 决定，需要写进 interface_spec.md**：

| 优先级 | 事件 | 说明 |
|---|---|---|
| 0 | `PAYMENT_ARRIVAL` | 新指令到达，进入队列 |
| 1 | `INFLOW_NOTICE` | 入账通知，**不增加余额** |
| 2 | `INFLOW_ARRIVAL` | 入账到账，增加 `rmb_total` |
| 3 | `FX_COMPLETION` | 换汇完成，人民币到账 |
| 4 | `PAYMENT_CONFIRMATION` | 付款确认，扣减余额、释放锁定 |
| 5 | `FUNDING_REVIEW` | 定期重评估（`funding_review_interval_sec`） |

同优先级按入库序号（`seq`）处理，保证完全可复现。

### 3.2 付款状态机

```
NOT_ARRIVED --到达--> QUEUED --提交(锁定)--> PENDING --确认成功--> SETTLED
                        ^                        |
                        +----确认失败(释放锁定)---+

eligible=false ------------------------------> INELIGIBLE（永不提交）
```

- 提交时只**锁定**，确认成功才扣减 `rmb_total`；失败释放锁定并回队列。
- `Available RMB = max(0, rmb_total - rmb_locked - rmb_reserve)`。
- `Available HKD = max(0, hkd_total - hkd_committed - hkd_liquidity_buffer)`。

### 3.3 FX 计价

```
有效汇率      = fx_rate × (1 - fx_spread_bps / 10000)
HKD 本金      = TopUpRMB / 有效汇率
HKD 手续费    = fx_fee_fixed_hkd
占用 HKD      = HKD 本金 + 手续费        （发起时占用，完成时扣减）
点差成本 RMB  = (HKD 本金 - TopUpRMB / fx_rate) × fx_rate
经济成本 RMB  = 点差成本 + 手续费 × fx_rate
```

`TopUpRMB = max(0, 目标付款金额 - AvailableRMB) + fx_buffer_rmb`，再夹到
`[fx_topup_min_rmb, fx_topup_max_rmb]` 之间；超出上限视为交易限额不满足。

---

## 4. 引擎自有参数（Module 2 的 config 里没有，需要全组确认）

| 参数 | 默认 | 理由 |
|---|---|---|
| `payment_confirmation_delay_sec` | 10 | proposal 4.1.4 的基线确认延迟；module2 config 未包含 |
| `payment_confirmation_fail_prob` | 0.0 | 基线不做确认失败 |
| `observation_drain_sec` | 0 | 观察终点 = `simulation_window_sec` + drain |
| `fx_buffer_rmb` | 0.0 | proposal 4.2.3 基线 buffer=0 |
| `fx_counterparty_capacity_mode` | `per_trade` | **口径解释见下** |

放入 `config.json` 的 `engine_parameters` 子对象或同名顶层字段即可覆盖。

### 需要拍板的两条口径

1. **`fx_counterparty_capacity_rmb` 是单笔上限还是全程累计额度？**
   本实现默认 **单笔上限**（与 `fx_topup_max_rmb` 同为交易限额）。
   若改成累计额度，把 `fx_counterparty_capacity_mode` 设为 `cumulative`，一行切换。
2. **`eligible=false` 怎么算？**
   本实现：不提交、不占用资金、终态记 `INELIGIBLE`，不计入完成率分子。
   是否计入完成率分母、是否计入违约率，需要模块6 一并确认。

---

## 5. 与模块4 / 模块5 的接线

模块3 只认两个函数：

```python
rank_payments(queue, state, config) -> {"order": [...], "scores": {...}}
decide_funding(queue, state, known_inflows, config, mode)
    -> {"action": "WAIT"|"TOP_UP"|"NO_ACTION", "target_payment_id", "topup_rmb", "reason"}
```

- 模块4 交 `module4_rank.py`、模块5 交 `module5_funding.py`，放进 `m3engine/`
  或 `PYTHONPATH` 即可自动接管；也可用环境变量 `M3_MODULE4_MODULE` /
  `M3_MODULE5_MODULE` 指定模块名。
- `queue` / `state` 里的金额是 `Decimal`，时间是 `int` 秒。
- **模块5 拿到的 `known_inflows` 只包含已通知、尚未到账、且在
  `max_lookahead_sec` 内的入账**，永远不是完整的 `inflows.csv`。
- 未提供真实实现时使用 `adapters.py` 的 fallback，`run_summary.json` 里
  `using_fallback_module4/5` 会标 `true`，CLI 也会打警告。

---

## 6. 验证状态

| 检查 | 结果 |
|---|---|
| 模块3 验收测试 | 13/13 通过 |
| 内置样例与 module2 原件 | 逐字节一致（有测试守住） |
| module2 `fixed_demo` 事件时间线 | 与 `expected_exogenous_events.csv` 逐条一致 |
| `P000001` 是否早于到达结算 | 否（这条测试抓出过一个真实 bug） |
| `I000001` 是否在 450 秒前变成可用资金 | 否 |
| 人民币 / 港币守恒 | pilot 全部策略精确到分 |
| pilot 21 个 dataset × 5 策略 | 105 次运行，0 失败 |
| formal 210 个 dataset × 5 策略 | 1050 次运行，0 失败，约 30 秒 |

---

## 7. 已知限制

1. **S2/S3/S4 不是模块3 的交付内容**，目前跑的是 fallback，数字不能进报告。
2. `fx_trades.csv` 是模块3 的补充输出，团队规范里没有这张表，需要模块6 确认
   是否采用；若不采用，FX 成本只能从 `reason` 字段里解析。
3. 事件时间全部为整数秒（遵循 module2 契约）；若将来引入亚秒级延迟，需要同步改
   `EVENT_ORDER` 的平局规则。
4. 余额快照在**每个事件**写一行，用于模块6 积分流动性小时数；单次运行约 1000 行。

---

## 8. 上传到 GitHub

用 `upload_to_github.py`，走 Git Data API，整个目录只产生**一个提交**。

### 8.1 先建 token

GitHub → Settings → Developer settings → **Personal access tokens → Fine-grained tokens**
→ Generate new token：

- **Repository access** 选 `Only select repositories`，勾上你的目标仓库
- **Permissions → Repository permissions → Contents** 设为 `Read and write`
- 有效期建议 7 天，交完作业就撤销

### 8.2 上传

```bash
export GITHUB_TOKEN=github_pat_xxx      # 只存在于当前 shell，不要写进任何文件

# 先看一眼会传什么（不联网）
python module3/upload_to_github.py --owner <你的用户名> --repo <仓库名> --dry-run

# 仓库已存在
python module3/upload_to_github.py --owner <你的用户名> --repo <仓库名>

# 仓库还不存在，顺手建一个私有仓库
python module3/upload_to_github.py --owner <你的用户名> --repo FTEC5510-Module3 --create
```

文件会落到仓库的 `module3/` 目录下，可用 `--dest` 改。

### 8.3 为什么不直接用 Contents API

`PUT /repos/{owner}/{repo}/contents/{path}` 一次只能写一个文件，9 个文件就是 9 个
commit，而且并发时容易互相覆盖。本脚本用的是：

```
blobs → tree(base_tree=当前tree) → commit(parents=当前commit) → PATCH ref
```

好处是**原子性**：任何一步失败，远端分支一动不动；而且是一次提交，历史干净。
`base_tree` 这一项很关键——漏了它会把仓库里已有的文件全部清掉。
