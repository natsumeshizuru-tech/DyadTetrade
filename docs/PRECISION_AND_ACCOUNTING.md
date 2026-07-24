# 精度与会计事实契约

## 三层精度

- 交易精度：价格必须是 `price_tick` 的整数倍，数量必须符合 `lot_size` 和 `quantity_step`。
- 会计精度：价格、成本、资金、费用和盈亏在 Python 使用 `Decimal`，数据库使用
  `NUMERIC/DECIMAL`，适配器入口消除 C double 尾差。
- 展示精度：API 优先返回十进制字符串；前端格式化不能反向修改后端事实。

证券元数据以 `price_tick`、`price_decimals`、`quantity_decimals`、`lot_size`、`currency` 和
`money_decimals` 为精度依据。订单提交前必须先量化价格和数量，再执行资金、持仓和风控检查。

## 费用事实优先级

```text
正式 BrokerSettlement 税费拆分
  > XtTrade.commission 券商聚合手续费
  > Paper/Backtest 明确费用模型
  > unavailable
```

`XtTrade.commission` 是当前 XtQuant 官方成交结构提供的聚合“手续费”字段。它保存到
`broker_reported_fee`，不能被拆成佣金、印花税和过户费。字段存在且为 0 与字段缺失具有不同含义：

- 存在且为 0：券商正式成交查询报告聚合手续费为 0；
- 缺失：`broker_reported_fee=NULL`、`fee_source=unavailable`；
- 两种情况都保持 `fee_breakdown_complete=false`；
- 两种情况都不能生成 `BrokerSettlement`。

当 `broker_reported_fee` 存在时，成交摘要的 `fees` 使用该聚合事实；当它不存在时，只有明确完整的
Paper/Backtest 费用组件才能相加。不得同时把聚合手续费和组件再次求和，避免双重计费。

## 会计不变量

已实现并自动测试：

```text
cash = available_cash + frozen_cash
total_assets = cash + positions_market_value + accrued
complete_component_fees = commission + tax + transfer_fee
partial_fill_average_cost = weighted notional and fees / resulting quantity
```

券商正式交割拆分尚未接入，因此 qmt_sim 的 `commission + tax + transfer_fee` 不得宣称等于
`broker_reported_fee`。取得正式交割事实后，才验证总费用拆分和净交收金额，并在不一致时进入
`RECOVERY` 或 `HALTED`、写对账事件，禁止静默修正。

## 当前验证证据

- 迁移：`20260713_12`、`20260713_14`、`20260713_19`。
- 自动测试：Decimal 尾差、价格 tick、部分成交成本、费用非负、券商字段缺失/负值、回填幂等。
- miniQMT 只读实机：55/55 笔 qmt_sim 成交取得 `XtTrade.commission`，8 位 Decimal 入账；
  当前模拟柜台聚合值合计为 0，重启后无重复成交和重复费用审计。
- 未完成：新 qmt_sim 成交的非零费用样本、正式交割单和税费拆分、日终人工会计复核。

结构化证据：
[`reports/validation/qmt_sim_broker_fees_20260713.json`](../reports/validation/qmt_sim_broker_fees_20260713.json)。
