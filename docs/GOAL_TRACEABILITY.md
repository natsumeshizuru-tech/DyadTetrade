# Development.md 目标追踪

更新时间：2026-07-13 20:58（Asia/Shanghai）

| 目标 | 状态 | 实现/迁移 | 自动测试 | 实机证据 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| 可信工程基线 | OPERATIONALLY_VALIDATED | `scripts/check_all.ps1`、`alembic/env.py`、`db/migration_url.py` | 空库重建、240 后端、5 前端、Ruff、mypy、build | miniQMT 只读和 API 冒烟通过 | 建立持续 CI |
| qmt_sim 查询订单进入 OMS | OPERATIONALLY_VALIDATED | `modules/execution/service.py`、`20260713_18_order_provenance.py` | runtime、converter、幂等/乱序测试 | 4 笔订单准确恢复 | 交易时段验证新订单 |
| broker order identity | OPERATIONALLY_VALIDATED | `xtquant_converters.py`、`20260713_20_trade_order_identity.py` | order_id=0、数字 order_sysid、独立持久化测试 | 4 订单/55 成交使用 order_sysid；误标 qmt_order_id 0；孤儿 0 | 增加不同柜台版本样本 |
| XtQuant 委托状态机转换 | OPERATIONALLY_VALIDATED | `XT_ORDER_STATUS` | 48—57、255 参数化测试 | 3 FILLED、1 CANCELLED、UNKNOWN 0 | 验证挂单/撤单过渡态 |
| QMT 回调与撤单竞态 | AUTOMATED_TESTED | `qmt_sim_gateway.py`、`execution/service.py` | 成交先到、同步撤单竞争、撤单失败、重复/乱序、查询状态倒退 | 只读复核无状态冲突；未发新单 | 用新活动订单实机验证部分成交/撤单 |
| 信号/订单/成交隔离 | INTEGRATION_TESTED | `orders`、`trades`、`broker_settlements`、`ledger_quarantine` | provenance、唯一性、来源守卫 | 错误哨兵订单已隔离 | 新订单四方一致性 |
| QMT 成交聚合手续费 | OPERATIONALLY_VALIDATED | `xtquant_converters.py`、`20260713_19_broker_trade_fees.py` | 缺失值、负值、精度、回填幂等测试 | 55/55 来自 `XtTrade.commission`；重启后仍唯一 | 用新成交复核非零费用样本 |
| BrokerSettlement | IMPLEMENTED | `20260713_12`、`20260713_13`、`20260713_19` | schema/API 和 provenance 测试 | 0 条正式交割 | 接入正式交割查询和税费拆分事实 |
| K 线连续性 | OPERATIONALLY_VALIDATED | `market_data/stitching.py`、`realtime/aggregator.py`、`realtime/service.py` | 六周期恢复、五分钟断线、回补/日历失败、累计量回退、午休、跨日和供应商标签测试 | 隔离目录实机六周期 1908 行、缺口 0；闭市快照不改 trusted 哈希 | 真实网络中断与跨日常驻验证 |
| 行情失败分类与隔离 | OPERATIONALLY_VALIDATED | `market_data/failures.py`、`20260713_21`/`22`、任务重试策略、活跃日线汇总 | 七分类、隔离重试、增量质量记录、指数/股票差异规则、迁移测试 | 187 条完成分类；9 条指数失败恢复；覆盖 7700/7779；交易账本零变化 | 清理剩余 79 个活跃缺口及错误目录成员 |
| Decimal 与会计精度 | AUTOMATED_TESTED | `domain/precision.py`、`20260713_12`、`20260713_14`、`20260713_19` | 成本、费用、部分成交、券商聚合手续费、前端格式测试 | 55 笔历史成交费用按 8 位 Decimal 回填 | 用新 qmt_sim 成交手工复核 |
| 风险状态机 | OPERATIONALLY_VALIDATED | `risk/state_machine.py`、`trading/engine.py`、`20260713_11` | 新 recovery epoch、受限状态重启、快速重连 latch、账户健康和活动订单不一致门禁 | HALTED/Kill Switch 跨 qmt_sim 重启保持；只读复核健康/一致 | 用活动订单验证断网重启并实机撤单 |
| 真实资金交易门禁 | AUTOMATED_TESTED | `core/config.py`、启动脚本 | fail-closed、双重开关和白名单测试 | qmt_live 数据行 0 | 始终保持关闭直到全部门禁满足 |

## 状态判定说明

- `OPERATIONALLY_VALIDATED` 只覆盖表格中明确写出的只读 miniQMT 行为，不代表完整 qmt_sim 新订单闭环。
- 自动测试使用临时数据库和临时数据目录；真实项目数据库只接受已备份、可回滚的增量迁移和只读对账回填。
- 券商 query/callback 是订单与成交事实来源；没有正式交割事实时不得生成 `BrokerSettlement`。
