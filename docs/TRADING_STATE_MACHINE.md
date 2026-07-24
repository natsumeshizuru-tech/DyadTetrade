# Trading Engine 风险状态与恢复契约

## 状态权限

| 状态 | 新开仓 | 减仓 | 撤单 | 说明 |
| --- | --- | --- | --- | --- |
| `NORMAL` | 允许 | 允许 | 允许 | 当前恢复 epoch 已成功对账 |
| `REDUCE_ONLY` | 禁止 | 允许 | 允许 | 只降低现有风险 |
| `CANCEL_ONLY` | 禁止 | 禁止 | 允许 | 只清理活动委托 |
| `HALTED` | 禁止 | 禁止 | 受控 | Kill Switch |
| `RECOVERY` | 禁止 | 禁止 | 受控 | 启动、重连、崩溃或不一致恢复 |

状态保存在 `trading_risk_states`，切换记录写入 `audit_events`。`HALTED` 不能直接进入
`NORMAL`，只能先进入 `RECOVERY`。

## Recovery epoch

每次进入或重新进入 `RECOVERY` 都创建新的恢复 epoch，并立即清空 `reconciled_at`。因此：

```text
旧 reconciled_at
  -> 进入 RECOVERY
  -> reconciled_at = NULL
  -> 直接 NORMAL 被拒绝
  -> 新一轮券商查询和账本一致性检查成功
  -> 写入新 reconciled_at
  -> 才允许恢复目标状态
```

`HALTED`、`CANCEL_ONLY`、`REDUCE_ONLY` 在重启对账成功后恢复原状态，不自动放宽为
`NORMAL`。正常停机只把 `NORMAL` 置为 `RECOVERY`；操作员设置的受限状态不会被停机覆盖。

## 活动订单一致性

成功对账除连接、订阅、资产健康外，还必须满足：

- 本地所有 `CREATED/SUBMITTING/SUBMITTED/PARTIALLY_FILLED/CANCELLING/UNKNOWN` 订单都能在
  当前券商活动订单集合中按 `qmt_order_id`、`order_sysid` 或 `client_order_id` 关联；
- 券商所有活动订单都能关联本地 OMS；外部券商订单先以明确 provenance 导入 OMS；
- 对账只使用本轮 `query_stock_orders` 快照；内存中的旧回调订单不能替代本轮券商事实；
- 本地活动订单在券商缺失、账户不健康或本地终态/成交量高于券商旧快照时失败关闭到 `HALTED`，
  分别写 `qmt_reconciliation_mismatch` / `qmt_order_state_regression`；同一事故重试不刷重复事件，
  恢复成功后同一差异再次出现会建立新事故；
- 若进程在 `order_stock_async` 返回 seq 后、broker order id 持久化前崩溃，重启通过
  `order_remark/client_order_id` 补回身份，不创建第二条 OMS 订单；API 将 `client_order_id` 限制为
  miniQMT `order_remark` 可完整保存的 48 字符。

`last_reconciliation_attempt_at` 表示最近尝试，`last_reconciled_at` 只表示最近一次成功；两者不能
混用。

断线和账户不健康会设置 recovery latch。即使网关在 watchdog 观察到 `connected=false` 之前已经
快速重连，latch 也不会由 `connect/subscribe` 清除；只有新的资产、持仓、委托、成交查询以及 OMS
一致性检查全部成功后才清除。成功发出撤单指令后订单进入 `CANCELLING`；期间成交量可继续增加，
`FILLED` 始终胜过撤单，撤单失败回到 `SUBMITTED` 或 `PARTIALLY_FILLED`。

## 启动与诊断

qmt_sim 在 `HALTED/RECOVERY/CANCEL_ONLY/REDUCE_ONLY` 下仍可启动 API 和前端供诊断，风险开关继续
拒绝新订单。qmt_live 必须处于 `NORMAL` 并满足全部独立发布门禁，否则启动失败。

## 验证状态

- 自动测试：五态转换、新 recovery epoch、三种受限状态重启、快速重连、账户不健康、本轮查询缺单、
  状态/成交量倒退、成交先于委托回调、部分成交/撤单竞争、撤单失败和提交崩溃窗口恢复。
- qmt_sim 实机：`HALTED` 后停机并重启仍为 `HALTED`，Kill Switch 保持；进入 `RECOVERY` 后直接
  `NORMAL` 返回 HTTP 409，新对账成功后恢复 `NORMAL`。
- 未完成：带真实 qmt_sim 活动订单的进程重启、部分成交/撤单竞争及断网恢复运行级验证。

结构化证据：
[`reports/validation/qmt_sim_risk_restart_20260713.json`](../reports/validation/qmt_sim_risk_restart_20260713.json)、
[`reports/validation/qmt_sim_recovery_races_20260713.json`](../reports/validation/qmt_sim_recovery_races_20260713.json)。

设计决策：[`ADR 0001`](adr/0001-qmt-order-fact-monotonicity.md)。
