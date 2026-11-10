# 固定样例来源说明

`fixed_demo/` 下的 5 个文件**原样复制**自 Module 2 仓库，未做任何修改：

| 项 | 值 |
|---|---|
| 来源仓库 | `FTEC5510-mBridge-Module2-Copy`（私有） |
| 仓库内路径 | `examples/fixed_demo/` |
| 文件 | `config.json`、`payments.csv`、`inflows.csv`、`expected_exogenous_events.csv`、`manifest.json` |
| `dataset_id` | `fixed_demo_v1` |
| `schema_version` | `1.0.0` |
| 规模 | 10 笔付款、3 笔入账，观察窗口 7200 秒 |

## 为什么复制进来

模块3 的验收测试需要一份**手工可核对**的输入。把样例放进本仓库有两个好处：

1. 别人 clone 下来就能 `python -m unittest` 跑通，不依赖 module2 仓库；
2. 样例版本跟着代码走，不会出现"代码换了、样例还是旧的"。

## 完整性约束

`manifest.json` 里记录了其余 4 个文件的 SHA256。`m3engine.io.load_dataset()`
默认会校验，只要任何一个文件被改动就会直接报错。

因此**不要手工编辑这个目录里的任何文件**。要换样例，从 Module 2 重新复制，
并确认 `python module3/run_engine.py --dataset module3/fixtures/fixed_demo --strategy S1 --dry-run`
能通过。

## 这是输入，不是结果

这份样例只包含外生事件（付款到达、入账通知、入账到账），**不含任何结算结果**。
结算结果由模块3 运行时产生：

```bash
python module3/run_engine.py --dataset module3/fixtures/fixed_demo --strategy all --out module3_outputs
```
