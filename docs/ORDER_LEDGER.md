# 委托与成交账本契约

## 身份边界

`orders.order_id` 是 DyadTetrade 本地 OMS 主身份；`qmt_order_id` 和 `order_sysid` 是券商身份。
`trades.order_id` 必须指向本地 OMS 订单，`broker_order_id` 和 `broker_trade_id` 保留券商事实。

```text
OrderIntent
  -> local OMS order_id
  -> qmt_seq
  -> positive qmt_order_id OR non-empty order_sysid
  -> broker trade id
```

不得把 `0`、空字符串或缺失值视为 broker identity。miniQMT 某些查询场景中数值 `order_id`
为 0，此时使用唯一 `order_sysid`；`order_sysid` 即使是纯数字字符串，也不能通过字符串形态推断为
`qmt_order_id`。订单与成交分别持久化原始 `qmt_order_id` 和 `order_sysid`；两者都不可用时拒绝写入
正式 QMT 成交账本并进入隔离流程。

## 来源

- `qmt_order_submit` / `dyadtetrade`：由本系统提交并在本地创建的订单。
- `qmt_order_query` / `qmt_broker`：启动或重连时从券商查询恢复的外部/历史订单。
- `qmt_trade_callback`：QMT 成交回调。
- `qmt_trade_query`：QMT 主动成交查询。
- `paper_order`、`paper_match`：Paper 账本，不能进入 QMT 正式成交/交割。
- `qmt_order_query_invalid`：缺少有效券商身份的隔离订单。

## 对账规则

1. 先按账户和 `qmt_order_id` 或 `order_sysid` upsert 券商订单。
2. 再按账户和 `broker_trade_id` 幂等 upsert 成交。
3. 将成交的本地 `order_id` 关联到对应 OMS 订单，同时保留原始 broker identity。
4. 身份修复必须写 `qmt_trade_order_link_repaired` 审计事件。
5. 查询到本地不存在的订单时创建 `qmt_external_order_observed` 审计事件，不伪称由本系统提交。
6. 券商聚合手续费首次出现或变化时写 `qmt_trade_fee_refreshed` 审计事件；重复查询不得重复写事件。
7. 不删除无法证明的历史记录；标记 `QUARANTINED` 并写 `ledger_quarantine`。
8. 回调和查询合并必须单调：终态不倒退、`filled_quantity` 不减少、`FILLED` 胜过撤单；状态或数量
   倒退写 `qmt_order_state_regression`，本轮对账失败关闭。
9. 每轮对账只以本轮券商订单查询快照判定活动订单集合；上一轮内存订单不得掩盖本轮缺失。
10. 同一订单先出现 `order_sysid`、后出现正数 `qmt_order_id` 时按两种 broker identity 联合关联，
    更新原 OMS 行而不是创建重复订单；查询和成交关联同时限定 `account_id + execution_mode`。

## 成交费用边界

当前安装的 XtQuant 官方 `XtTrade` 结构提供 `commission`，文档含义为“手续费”。该字段进入
`trades.broker_reported_fee`，同时记录：

```text
fees = broker_reported_fee
fee_source = xttrade.commission
fee_breakdown_complete = false
data_origin = qmt_broker
```

`commission`、`tax`、`transfer_fee` 组件仍保持未推断状态。券商返回 0 时，0 是已报告的聚合事实，
但不代表已证明各费用组件分别为 0；字段缺失时保存 `NULL`，不能把缺失冒充成券商返回 0。

## 交割边界

`BrokerSettlement` 只能来自券商正式交割查询或正式回报。成交查询不包含完整佣金、印花税、
过户费时，不得仅凭成交金额推造交割记录。当前 `broker_settlements` 为空是诚实状态，不是数据缺失填零。
