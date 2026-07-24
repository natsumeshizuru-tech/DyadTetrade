# 行情下载失败分类与隔离策略

更新时间：2026-07-13 20:58（Asia/Shanghai）

## 目标

行情任务不能把空数据、退市证券、伪证券、质量拒绝和供应商连接异常都压成同一个
`download_failed`。每条失败项必须保留分类、证据、重试资格、下次重试时间和隔离时间；默认重跑不得
唤醒永久失败项。

## 七类状态

| 分类 | 判定证据 | 默认重试 | 隔离 |
| --- | --- | --- | --- |
| `not_listed_yet` | `list_date > task.end_date` | 否；重新规划上市后区间 | 是 |
| `delisted` | 退市日早于请求区间，或质量异常标的同时不存在于当前目录和供应商详情 | 否 | 是 |
| `no_market_data` | 已上市、受支持标的由供应商返回空行情 | 否；等待目录/供应商事实变化 | 是 |
| `temporarily_unavailable` | 连接、超时、临时不可用 | 是，5 分钟冷却 | 否 |
| `quality_rejected` | OHLC、股票成交量/额等规范化门禁拒绝 | 否；先修复规则或数据 | 是 |
| `unsupported_instrument` | 认购/申购/赎回款、债券误入股票目录或 `other` 类型 | 否 | 是 |
| `provider_error` | 未归入上述类别的供应商异常 | 是，30 分钟冷却 | 否 |

任务 API 的默认 `retry_failed=true` 只释放到期、可重试且未隔离的项目。只有显式传入
`include_quarantined=true` 才能解除永久隔离；该操作仍保留原分类和质量报告作为审计证据。

## 指数成交量口径

miniQMT 对部分指数的成交量或成交额使用负数表示字段不可用。原实现套用股票规则，导致价格完整的指数
日线被整体拒绝。当前管线按标的类型处理：

- raw 层原样保存供应商负数哨兵值；
- clean/trusted/bt_ready 层只将发生负数哨兵的字段规范化为 0，另一成交字段如仍有效则原样保留，
  避免违反 `Bar` 非负约束或丢失有效成交额；
- 对应行明确标记 `quality_status=price_only`，并生成 warning
  `index_volume_amount_unavailable`；
- 股票和 ETF 的负成交量/额仍是 error，不能借指数例外绕过质量门禁。

## 真实数据迁移结果

迁移 `20260713_21` 前使用 SQLite backup API 备份真实库，并保存 7 个受影响指数的已有 Parquet；
`20260713_22` 将遗留 `quality_issues.download_failed` 同步为同一分类。187 条历史失败全部被分类；
随后只显式释放 9 条指数质量失败，9/9 成功：

- 失败项由 187 降至 178；剩余为 `delisted=8`、`no_market_data=92`、
  `unsupported_instrument=78`，全部保持隔离；
- 活跃日线覆盖使用 `active instruments INNER JOIN trusted 1d` 的一致口径，覆盖由
  7698/7779 提升到 7700/7779，缺失由 81 降到 79；
- 7 个指数 trusted 共 12,521 行，raw 中 670 行负数哨兵被保留，trusted 中负数为 0，
  `price_only` 为 670 行；这 670 行的有效 amount 与 raw 逐行比对差异为 0；
- 数据库 `PRAGMA integrity_check=ok`；迁移前后 qmt_sim 为 5 订单/55 成交，qmt_live 为
  0 订单/0 成交，交易账本完全未变。

结构化证据：
[`reports/validation/market_data_failure_classification_20260713.json`](../reports/validation/market_data_failure_classification_20260713.json)。
