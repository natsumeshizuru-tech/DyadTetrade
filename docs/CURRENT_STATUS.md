# DyadTetrade 当前状态

> 2026-07-24 更新：产品 API 与两个前端新增统一 XtData 下载/导入控制面，目录覆盖官方历史周期与财务、板块、历史合约、节假日、可转债、ETF、指数权重类别；Research 新增因子/策略新建及 VS Code 打开/同步；交易端新增仅可在 HALTED/RECOVERY 调低启动上限的前端风控配置。MiniQMT 实机已跑通 `000001.SZ` 2026-07-23 至 2026-07-24 日线下载并导入共享 SQLite；Research 106 项、Tetrade 309 项后端测试和前端 7 项测试通过，两个浏览器页面无 console error，全程未发送订单。

> 2026-07-22 更新：产品启动入口已收敛为交易专用 API（49 条路径），研究、历史数据、因子、回测、优化和策略文件编辑不再注册；前端收敛为交易、实时行情、策略监控/发布、系统四个工作区。Research → Trading 只允许经 Schema、SHA-256、数据能力、风险预算与审批级别验证的 `StrategyReleaseBundle`，可绑定当日执行股票池登记为待激活草稿。全量回归 304/304 通过，Ruff、格式、strict mypy、前端 7/7 测试、ESLint 与生产构建通过。下文保留的是较早的 miniQMT 运维证据与缺口记录；本轮没有发送或撤销任何真实/模拟柜台订单。

更新时间：2026-07-14 02:34（Asia/Shanghai）

本文件只记录由当前代码、数据库、自动测试或 miniQMT 实机结果支持的事实。状态枚举为
`PLANNED`、`IMPLEMENTED`、`AUTOMATED_TESTED`、`INTEGRATION_TESTED`、
`OPERATIONALLY_VALIDATED`。

## 安全状态

- 默认配置仍为 `DYADTETRADE_ENABLE_LIVE_TRADING=false` 和 `execution_mode=disabled`；当前进程经当日
  人工门禁显式运行在 `qmt_live`。
- 本轮 `qmt_live` 连接的是 QMT 模拟资金账户；只进行了启动、查询、订阅和对账，没有发送或撤销订单。
- 行情连续性实机复核使用 `execution_mode=disabled` 和隔离数据目录，只调用 miniQMT 行情接口。
- 本次对账新增导入 54 条历史 `qmt_live` 委托事实、0 条新成交事实；这些记录均为
  `is_simulated=true`，不是本次新发订单。
- Trading Engine、API 和前端当前在线；连接、订阅、账户健康、账本一致性和发布门禁均为 true，
  风险状态为 `NORMAL`。
- 当日 `qmt_live` 动态执行池 `universe-20260714-a99e6159740b` 已复核并激活，覆盖沪深股票和 ETF
  共 5768 只；自动策略仍关闭，重启前后 QMT 委托和成交事实数量未增加。

## 工程基线

| 项目 | 状态 | 当前证据 |
| --- | --- | --- |
| 空数据库从零迁移到 head | AUTOMATED_TESTED | `20260713_22 (head)`；`alembic check` 无差异 |
| 测试存储隔离 | AUTOMATED_TESTED | `check_all.ps1` 使用独立 SQLite、DuckDB、数据和策略目录 |
| 后端测试 | AUTOMATED_TESTED | 245 passed；本次全量测试使用隔离数据库 |
| Python 静态检查 | AUTOMATED_TESTED | Ruff、格式检查、strict mypy 118 个源码文件通过 |
| 前端 | AUTOMATED_TESTED | 5 tests、ESLint、TypeScript/Vite production build 通过 |
| miniQMT 只读状态 | OPERATIONALLY_VALIDATED | qmt_live 连接 QMT 模拟资金账户；订阅、账户健康、对账、NORMAL 风险状态和发布门禁均通过；未发送订单 |

首次隔离全量检查曾发现 3 个策略测试依赖真实 `bt_ready` 行情；测试现使用显式临时 Parquet
fixture，子进程也继承隔离数据目录。

## qmt_sim 委托与成交账本

| 项目 | 状态 | 当前证据 |
| --- | --- | --- |
| 本地订单提交和回调持久化 | INTEGRATION_TESTED | 异步响应、成交先到、重复/乱序、撤单失败和部分成交/撤单竞争测试通过 |
| 券商查询订单导入 OMS | OPERATIONALLY_VALIDATED | 4 笔查询订单：3 `FILLED`、1 `CANCELLED` |
| 券商成交身份 | OPERATIONALLY_VALIDATED | 55/55 有唯一 `traded_id` 和有效 broker order identity |
| 成交到 OMS 订单关联 | OPERATIONALLY_VALIDATED | 55/55 已关联；孤儿数 0；4 个不同 broker order |
| 券商成交聚合手续费 | OPERATIONALLY_VALIDATED | 55/55 读取正式 `XtTrade.commission`；均按 Decimal 入账并标记拆分不完整；本柜台合计返回 0 |
| 风险状态跨重启保持 | OPERATIONALLY_VALIDATED | qmt_sim 实机 `HALTED` 停机/重启后仍为 `HALTED`，Kill Switch 保持开启；诊断界面可用 |
| 活动订单重启一致性门禁 | AUTOMATED_TESTED | 当前查询快照缺单、终态/成交量倒退及账户不健康均失败关闭；崩溃窗口与 broker identity 升级不重复建单 |
| 错误账本隔离 | OPERATIONALLY_VALIDATED | 1 条旧哨兵订单标记 `QUARANTINED`，隔离证据保留 |
| 新委托、撤单、成交闭环 | IMPLEMENTED | 本轮未在交易时段人工发送新 qmt_sim 委托，不能升级状态 |
| BrokerSettlement | IMPLEMENTED | 表和接口存在；当前正式交割记录 0，不能冒充已验证 |

miniQMT 当前环境的历史查询订单使用 `order_id=0`，但提供唯一 `order_sysid`。适配器现采用：

```text
positive order_id -> broker order identity
otherwise non-empty order_sysid -> broker order identity
otherwise reject and quarantine
```

`order_sysid` 即使只包含数字也不能据此推断成 `qmt_order_id`。迁移 `20260713_20` 为成交增加
独立 `qmt_order_id`/`order_sysid` 字段；实机纠正后 4/4 查询订单和 55/55 成交均明确使用
`order_sysid`，误标的 `qmt_order_id` 数量为 0，孤儿和重复成交仍为 0。

XtQuant 官方状态码 48—57 和 255 已完整映射；修复后实机查询无 `UNKNOWN` 活动订单。

当前安装的 XtQuant 官方文档将 `XtTrade.commission` 定义为“手续费”。迁移
`20260713_19` 将其保存为独立 `broker_reported_fee`，并以 `fee_source=xttrade.commission`、
`fee_breakdown_complete=false` 明确它只是聚合券商事实。系统不会把该字段错误拆成佣金、印花税或
过户费。工作台停止后再次启动，对账仍保持 55 个唯一成交、0 个重复身份、0 个孤儿成交，费用审计
事件没有重复增加。

风险恢复现在以每次进入 `RECOVERY` 为新的对账 epoch：旧 `reconciled_at` 必须清空。本轮 qmt_sim
实机将状态切到 `HALTED` 后停机并重启，状态和 Kill Switch 均保持；随后进入 `RECOVERY`，直接切回
`NORMAL` 返回 HTTP 409，只有新的资产、持仓、订单、成交及活动订单一致性检查通过后才恢复
`NORMAL`。全程无委托、撤单或真实资金动作。

订单恢复进一步采用单调事实规则：成交回调可先于委托回调推进订单；成功发出撤单指令后进入
`CANCELLING`，撤单期间的新成交只增加成交量而不丢失撤单意图，撤单失败则恢复到对应活动状态；
`FILLED/CANCELLED/REJECTED` 和已观察成交量不会被较旧查询结果倒退。查询对账只使用本轮券商快照，
不能用内存中上一轮订单伪造一致；快速重连和账户健康恢复都保留 recovery latch，完成新对账后才清除。

19:40 的 qmt_sim 实机只读复核显示：连接、订阅、账户健康和账本一致性均为 true，活动订单 0、
状态冲突 0；API 仍为 4 笔券商订单/55 笔成交。与启动前备份比较，真实库 qmt_sim 订单和成交增量
均为 0，`qmt_order_state_regression` 与 `qmt_reconciliation_mismatch` 均为 0，qmt_live 订单/成交仍为 0。

## 数据状态

- 最新交易日：`20260713`，与交易日历预期一致。
- 活跃标的日线覆盖按 `active instruments INNER JOIN trusted 1d` 统一口径统计为
  7700/7779，缺失 79；最新交易日覆盖 7686/7779。旧汇总把未注册/非活跃文件计入分子，显示的缺失
  73 不可继续作为覆盖证据。
- 187 条历史失败已由迁移 `20260713_21` 分类并隔离，`20260713_22` 同步旧质量问题口径。修复指数成交量口径后，9 条指数质量失败
  全部恢复，失败降至 178：`delisted=8`、`no_market_data=92`、
  `unsupported_instrument=78`；默认任务重跑不会唤醒这些永久失败。
- 7 个修复指数 trusted 共 12,521 行；raw 保留 670 行供应商负成交量/额哨兵，trusted 将其明确
  标记为 `price_only` 并保持价格历史可用；有效 amount 逐行差异为 0，股票/ETF 的负成交量门禁不变。
- `510300.SH` 在 `20260713` 的不复权实机 API 回补/拼接结果：1m 241 根、5m 48 根，
  09:30/09:35 至 15:00，键唯一、无缺口、全部 finalized、无 backfill error。
- 5m/15m/30m/1h 的首个完整 Bar 已修正为开盘后一个完整周期；1m 保留 miniQMT 的 09:30 标签。
- 实时服务启动、轮询异常和 watchlist 扩展均先对 1m/5m/15m/30m/1h/1d 六周期执行尾部回补；
  任一周期或官方交易日历失败都会保留恢复状态并阻止行情被标记为 fresh。
- 实时分钟标签与 miniQMT 本地标签一致；跨日累计量重置、同日累计量回退、旧 Tick 和非交易日 Tick
  均有单调门禁。恢复后的首个残缺周期以及闭市后首次快照不会覆盖已回补的 finalized 历史。
- 20:08 在隔离目录对 `510300.SH` 完成 miniQMT 六周期回补：最近 6 个交易日分别为
  1m 1446、5m 288、15m 96、30m 48、1h 24、1d 6 行，六周期缺口均为 0；闭市快照前后
  trusted Parquet 哈希无变化。自动故障注入已覆盖五分钟断线、回补失败、交易日历失败、午休和跨日；
  尚未进行真实网络中断和跨日常驻进程演练，不能标记为完整全天运行验证。

## 当前 P0 缺口

1. 在交易时段以一只白名单高流动性 ETF、一手、明确限价完成人工确认的 qmt_sim 新挂单、撤单和成交。
2. 用上述新订单验证异步 seq、order_id/order_sysid、回调、SQLite、前端和 miniQMT 四方一致。
3. 自动门禁已覆盖活动订单缺失、提交崩溃窗口、快速重连、账户不健康、部分成交/撤单竞争和状态倒退；
   仍需用新 qmt_sim 活动订单完成 Trading Engine 实机重启、断网恢复和四方对账。
4. 接入正式交割单以及佣金、印花税、过户费的拆分事实；当前只有 `XtTrade.commission` 聚合字段，
   在取得正式交割事实前保持 `broker_settlements` 为空。
5. 失败七分类、隔离重试策略和指数口径已完成；继续清理 ETF 伪证券/无行情目录并解释剩余 79 个
   活跃标的缺口。自动化运行级断线、午休和跨日门禁已完成，仍需在交易时段执行真实网络中断以及
   跨日常驻进程验证。

本轮结构化证据见
[`reports/validation/qmt_sim_reconciliation_20260713.json`](../reports/validation/qmt_sim_reconciliation_20260713.json)
和
[`reports/validation/market_stitching_20260713.json`](../reports/validation/market_stitching_20260713.json)，
券商手续费回填与重启幂等证据见
[`reports/validation/qmt_sim_broker_fees_20260713.json`](../reports/validation/qmt_sim_broker_fees_20260713.json)，
风险状态实机重启证据见
[`reports/validation/qmt_sim_risk_restart_20260713.json`](../reports/validation/qmt_sim_risk_restart_20260713.json)，
恢复与竞态自动门禁及只读实机复核见
[`reports/validation/qmt_sim_recovery_races_20260713.json`](../reports/validation/qmt_sim_recovery_races_20260713.json)，
六周期行情恢复证据见
[`reports/validation/market_continuity_recovery_20260713.json`](../reports/validation/market_continuity_recovery_20260713.json)，
失败分类、指数修复和交易账本零变化证据见
[`reports/validation/market_data_failure_classification_20260713.json`](../reports/validation/market_data_failure_classification_20260713.json)。

## 2026-07-14 账户资金与持仓事实修正

- `qmt_live` 的可用资金直接采用 `query_stock_asset()` 返回的 `XtAsset.cash`，不再重复扣减
  `frozen_cash`；持仓市值与总资产采用 QMT 报告值。
- 每次报单进入风控前都会重新调用 `query_stock_asset()` 和 `query_stock_positions()`；买单按最新
  `available_cash` 校验，卖单按最新 `can_use_volume` 校验。任一查询失败均拒绝报单。
- 当 `query_stock_orders()` 的当日列表缺少本地已知活动委托时，会再按 QMT 订单编号调用
  `query_stock_order()`；只有柜台返回明确状态才更新本地账本，查不到仍保持对账失败关闭。
- 08:44 只读验收：账号 `***4967`，`qmt_live` 已连接、订阅、账户健康、账本一致，风险状态
  `NORMAL`；可用资金 `5673.15`，冻结资金 `0.00`，持仓 15 条，持仓市值 `8898198.17`，
  QMT 总资产 `11414577.42`。
- 15 条前一日遗留委托在当日列表和逐单查询中均不存在，且冻结资金已释放；备份数据库后已按
  柜台证据审计化收敛为 `CANCELLED`。本轮未调用下单或撤单接口，当前活动委托为 0。
