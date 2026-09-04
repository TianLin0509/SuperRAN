# 2026-09-04 KPI 口径 — buffer 在发送时扣减，不看这个 TB 对不对

**分支 / SHA**：`feat/buffer-drain-on-tx-20260904` / `<提交后回填>`　**风险档**：红
**报告**：`C:\VibeData\Artifacts\Reports\SuperRAN\20260904-buffer-drain-on-tx.html`

## 改了什么物理机制

DRB busy period 的**记账时刻**，也就是体验速率 KPI 的分子分母怎么产生。

改前：`DrbQueue.transmit()` 只有在 ACK 时才扣 `queued_bytes`、累加 `bytes_acked`、
记 `last_ack_tti`、追加 `AckEvent`。busy period 在**最后一个 ACK** 结束。
被 NACK 的 TB 其字节留在队列里，等重传 ACK 才算送达。

改后（用户 2026-09-04 给的现场统计口径）：

1. **发出一个包后 buffer 空了，这个包的 KPI 当场就能统计**——掐头去尾业务量与
   掐头去尾时间——**完全不管这个包正确与否**。不等 ACK。
2. 误码与重传对体验速率的影响，主要体现在**重传要占资源**。
3. **重传优先级高于新传。** 发完这个包 buffer 还没空时，时间继续统计；NACK 回来时
   如果 buffer 还没空，就得先重发误码的包，把后面的数据往后推，掐头去尾时间被拉长。

对应到代码：`transmit(..., is_retx=False)` 在**发送时**扣队列，busy period 在
"清空 buffer 的那一次发送"结束（`last_tx_tti`），`TxEvent` 只记首传。
重传走 `is_retx=True`：只累加 `tx_attempts`（资源占用的证据），
**不动队列、不产生 TxEvent、不推进 busy period**。

`BusyPeriod` 的字段随语义改名（`bytes_acked→bytes_sent`、`last_ack_tti→last_tx_tti`、
`ack_events→tx_events`），旧名保留为只读属性别名；`AckEvent` 保留为 `TxEvent` 的别名。

## 为什么

两个独立的理由。

**一，这是现场的统计方式。** 改前的模型把"传对了"当成"送达"的前提，于是一个
TB 被 NACK 会直接延长该用户的 busy period（反馈时延 2~6 个 TTI + 重传 + 再一个
反馈时延）。现场不是这么算的。

**二，多进程 HARQ 必须靠它才自洽。** 这不是 KPI 偏好问题。按 ACK 扣减时，
被 NACK 的 TB 其字节仍留在队列里，同一个 UE 的**另一个** HARQ 进程会把
**同一批字节**再组成一个新 TB 发一遍；等原 TB 的重传轮到时，队列已经不够冻结的
payload，硬校验直接失败。实测轨迹（4 UE / ftp3 / 8 进程）：

```
t3025  发 33822 B  NACK   队列 105955 → 105955（不减）
t3026  发 33822 B  ACK    队列 105955 →  72133   ← 同一批字节又发了一遍
t3027  发 33822 B  ACK    队列  72133 →  38311
t3028  发 20497 B  ACK    队列  38311 →  17814
       t3025 的重传到期：需要冻结的 33822 B，队列只剩 17814 B → RuntimeError
```

## 证据

### 三条合同各有一条棘轮（`tests/test_system.py` 第 18 节）

- 合同 1：`transmit(2, 120, 100, ack=False)` 返回 100 且队列归零，busy period
  在 `last_tx_tti=2` 结束——NACK 不阻止记账。
- 合同 2：`transmit(..., is_retx=True)` 返回 0、队列不动、`tx_attempts` 加 1 但
  `tx_events` 不增。
- 合同 3（端到端，`tdd_pattern="D"`，强制首传全错）：

  | 轨迹 | `cell_served_mbps` | `residual_bler` |
  |---|---|---|
  | 首传全对 | 368.8 | 0.0 |
  | 首传全错、重传全对 | **184.4** | 0.0 |
  | 首传全错、重传也全错 | **184.4**（逐值相同） | 1.0 |

  第二行掉一半 = 重传吃掉的资源。第三行与第二行**逐值相同** = KPI 不看对错。

**红态实测**：把 `transmit` 换回"只有 ACK 才扣队列"，第 18 节 4 条同时变红
（`实得 sent=0、剩余=100` 等）；恢复后全绿。

### 顺带修正的一条断言

`test_system` 原来用 `cell_served_mbps` 比较 IR 与 CC 的优劣。新口径下两者的已发送
字节**逐值相同**（184.424），因为它们发出去的首传一样多——拿吞吐比 IR/CC 变成空断言。
改为比 `retx_bler` 与 `residual_bler`（0.0 vs 0.025），并显式断言两者的
`cell_served_mbps` 相等。这是把断言挪到真正承载该物理量的 KPI 上，不是放宽。

### 回归

test_system / test_physics_invariants / test_csi_aging / test_rng / test_e2e /
test_results（`__main__` 入口，退出码 0）；test_scheduler_p0 19、test_scheduler_edf 45、
test_linkadapt 4、test_carrier 33、test_power_control 13、test_mcp_server 1、
test_developer_guide 11 passed。ruff 回到 develop 基线的 2 处。

## 没证明什么

- **没有量化这次口径改动让各条历史 KPI 变了多少。** 只做了机制级的最小反例，
  没有在真实场景配置上跑改前/改后的成对实验。所有历史体验速率数字都要重新标定。
- **末次重传失败的字节被计为已送达。** 它们只出现在 `residual_bler` 里，不回队列、
  不重新变成新 TB。这与改前的行为（`payload 留队并在后续成为新 TB`）不同，
  也是现场口径的直接后果——但**没有验证现场对"末次失败"的处理是否也是这样**，
  真实系统里那是 RLC ARQ 的事，本模型不建 RLC。
- **`attempted_payload` 仍把重传的 payload 计进去。** 它是"尝试发了多少 payload"
  的诊断量，不是已送达字节；两者现在会差一个重传量，读的时候要分清。
- **删掉了两道硬校验**（`scheduler_finalize` 与 `_build_su_plan` 里的"队列必须 ≥
  冻结 payload"）。它们在新口径下恒不成立，但删掉也意味着少了一道防线；
  重传身份的其余校验（mcs / n_prb / rank / tb_bytes）都还在。
- 没有改 `legacy_v1` 的 `trim` 口径（那是历史复现路径，另一套记账）。
- 没有改 capacity 路径的 `_Traffic.serve`——它同样是"ACK 才扣"，但 capacity 是全带
  单 grant 模型，**本次没有量化它的同类偏差**。

## 影响哪些 KPI

**所有 experience 体验速率 KPI 全面变化**，方向是"不再因为传错而惩罚两次"：
busy period 变短、掐头去尾时间变短、体验速率上升；已发送字节不再随 BLER 波动。
`residual_bler` 的重要性上升——它现在是唯一反映"传丢了多少"的 KPI。

**改前的所有体验速率结论都不能与改后的数字放进同一张趋势图。**
