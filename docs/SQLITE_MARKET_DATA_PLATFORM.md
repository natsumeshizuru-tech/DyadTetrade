# miniQMT SQLite 数据与历史回测平台

2026-07-18 的实机恢复结果、真实数据规模和验收证据见
[SQLITE_MARKET_DATA_ACCEPTANCE_20260718.md](SQLITE_MARKET_DATA_ACCEPTANCE_20260718.md)。

## 事实源与安全边界

这条链路只把 SQLite 视为行情、公司行为、任务水位和回测结果的事实源：

- `data/sqlite/catalog.db`：证券、交易日历、公司行为、任务/子任务、水位、质量问题、分片与归档清单。
- `data/sqlite/bars_1d.db`：全市场原始日线，主键 `(symbol, ts)`。
- `data/sqlite/bars_1m_{market}_{YYYYMM}.db`：按市场和自然月分片的原始 1 分钟线。
- `data/sqlite/fundamentals_{YYYY}.db`：按报告期年度分片的全字段财务记录。
- `reports/backtests/sqlite/*.db`：隔离的历史回测结果库。

所有 XtData 读取强制使用 `dividend_type=none`、`fill_data=False`。五种复权仅在查询时由
`corporate_actions` 动态计算，绝不回写原始 OHLC。`dyadtetrade.data` 包不导入 XtTrader，
不包含下单、撤单、账户或持仓查询接口。

项目主体是 Python 3.12；当前国金 QMT SDK 只有 CPython 3.11 原生扩展，因此另建
`.xtdata-venv`。普通命令由 3.12 主进程调用只读 3.11 边车；大批量下载用 3.11 进程内快路径，
避免重复进程启动和 DataFrame 序列化。两条路径访问的是同一个普通行情客户端数据目录。

## 当前机器配置

- QMT SDK：本机授权 QMT 安装目录下的 `bin.x64/Lib/site-packages`
- QMT 行情缓存：本机授权 QMT 的 `userdata_mini/datadir`
- Python 3.11 环境：`.xtdata-venv/Scripts/python.exe`
- 配置：`config/market_data.yaml`
- SQLite：`data/sqlite`
- 归档：`data/archive/market_data`

标准只读诊断：

```powershell
.venv\Scripts\python.exe -m dyadtetrade.data.cli doctor
```

大批量进程内诊断（系统执行策略关闭时需显式 Bypass）：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\run_bulk_data.ps1 doctor
```

## 初始化、回填与断点恢复

```powershell
.venv\Scripts\python.exe -m dyadtetrade.data.cli init-db

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\run_bulk_data.ps1 backfill `
  --start 20240101 --end 20260718 --periods 1d --include-delisted

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\run_bulk_data.ps1 backfill `
  --start 20250716 --end 20260718 --periods 1m --include-delisted

# 导入大QMT已经下载到本机的分钟DAT（不启动交易客户端）
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\run_bulk_data.ps1 import-qmt-dat `
  --start 20240101 --end 20250715 --markets SH,SZ

# 八类可读财务表，完整保留厂商返回的所有字段
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\run_bulk_data.ps1 backfill-financial `
  --start 20240101 --end 20260717 --resume

# 当前板块成员和当前指数权重快照；中断后加 --resume
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\run_bulk_data.ps1 sync-reference-data `
  --snapshot-date 20260717 --resume
```

请求结束日先按交易日历裁剪；2026-07-18 的实际结束日是 2026-07-17。每只证券再按上市/退市
日期裁剪。恢复不信任单一全局水位，而是直接查询目标分片主键：历史前缀缺失则从请求开头补，
已有尾部则回扫最后 3 个交易日。UPSERT 只在 `source_hash` 改变时更新，因此重复任务可证明
`inserted=0, updated=0`。

每个下载批次都检查 F 盘 SQLite 根目录至少保留 20 GiB、QMT 缓存盘至少保留 10 GiB。
批量下载失败会降级到单证券；下载后无行还会再走一次厂商单证券接口，然后才记为失败。

## 上游可用性事实

2026-07-18 在当前 QMT 权限和缓存上实测：

- 日线可覆盖 2024-01-02 至 2026-07-17。
- miniQMT 对沪深北代表证券重新请求 2024-01-01 至 2025-07-15 后仍返回 0 行。
- 大QMT根目录的本地 `DAT` 文件经格式校验和重叠行情逐字段对照后，补得 2025-07-11 至
  2025-07-15 共 603,710 条；本机大/miniQMT均没有 2024-01-01 至 2025-07-10 的分钟文件。
- miniQMT原有数据从 2025-07-16 开始；此后月份可正常下载。
- `download_history_contracts()` 成功返回，但板块列表没有任何“过期”或“退市”板块，因而不能
  从该终端取得退市证券全集。

这些缺口会写入验收报告，绝不使用前值填充、日线合成分钟线或虚构退市样本。将来若 QMT 权限
或本地缓存补齐，只需用原始请求起点重跑；主键恢复逻辑会只补缺失前缀。

QMT 的“京市A股”板块还混入了 `ProductID=C/R` 的定转和债券及两个指数。系统使用严格 A 股
代码范围并将这些标的标为 `excluded_non_equity`；范围清理证据位于
`reports/data/scope_reconciliation_20260718.json`。

## 财务、板块和指数权重

当前运行库可读取并落库八类财务表：`Balance`、`Income`、`CashFlow`、`Capital`、
`Top10FlowHolder`、`Top10Holder`、`HolderNum`、`PershareIndex`。每条记录保存证券、表名、
报告期、公告日、同报告期序号、完整规范化 JSON、来源和内容哈希；同一报告的股东明细减少时会
删除旧序号，重复运行不会累积脏行。查询同时使用 `report_time` 和 `announce_time` 两种上游筛选
后合并去重，最终范围以报告期 2024-01-01 至 2026-07-17 为准。

厂商下载函数还列出 `PerShare`，但当前 `get_financial_data` 运行时代码没有该表映射，沪深北
实测均返回空表，因此报告将其标为“不受当前上游读取接口支持”。当前版本还会让部分
`m_quarter` 返回未初始化的大整数；系统只接受 1～4，其余归一为空，字段仍保留，避免每次下载
产生假修订。

`catalog.db` 还保存 `sector_catalog`、全量 `sector_snapshots`、`index_weight_catalog` 和
`index_weight_snapshots`。XtData只暴露当前成员与当前已下载权重，不提供历史生效区间，因此
系统不会把2026年查询到的成分伪写成2024/2025历史快照。回测若需要历史指数成分，必须接入有
生效日期的外部数据源，并明确记录 `source` 和差异报告。

## 本地 XtData 兼容查询

```powershell
.venv\Scripts\python.exe -m dyadtetrade.data.cli query `
  --symbols 600000.SH,000001.SZ --period 1d `
  --start 20240101 --end 20260717 --adjust front_ratio
```

`LocalXtData` 支持 `get_market_data_ex`/`get_market_data` 风格调用、精确时间边界、`count`、
交易日历、板块、证券详情和除权因子。原生存储只有 `1d/1m`；`5m/15m/30m/1h` 按午休分段
从 1 分钟线确定性聚合，周/季/年从日线聚合。

支持的动态复权：`none`、`front`、`back`、`front_ratio`、`back_ratio`。成交量和成交额不因
价格复权而改变。

## QMT 风格历史回测

```powershell
.venv\Scripts\python.exe -m dyadtetrade.data.cli backtest-double-ma `
  --symbol 600000.SH --period 1d --start 20240101 --end 20260717 `
  --fast 5 --slow 20 --quantity 1000
```

回测按当前 bar 调用 `handlebar(ContextInfo)`，策略只能看见当前及过去数据；订单在下一根 bar
撮合。历史经纪商实现买卖费率、最低佣金、卖出印花税、滑点、100 股整数手、T+1、停牌、
涨跌停和资金/持仓拒单。订单、成交、持仓、净值、日志和配置写入独立结果 SQLite。

## 校验、EOD、计划任务与归档

```powershell
.venv\Scripts\python.exe -m dyadtetrade.data.cli validate --full
.venv\Scripts\python.exe -m dyadtetrade.data.cli eod-sync

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\install_eod_task.ps1

.venv\Scripts\python.exe -m dyadtetrade.data.cli archive verify
.venv\Scripts\python.exe -m dyadtetrade.data.cli archive seal `
  --path data/sqlite/bars_1m_SH_202606.db
.venv\Scripts\python.exe -m dyadtetrade.data.cli archive restore `
  --path data/archive/market_data/minutes/202606/bars_1m_SH_202606.db `
  --target data/restore-check/bars_1m_SH_202606.db
```

EOD 使用互斥锁，只处理交易日并回扫 3 个交易日；校验覆盖率、执行 `PRAGMA optimize` 和 WAL
checkpoint，在线备份 catalog。跨月后自动封存上月三市场分钟分片，执行完整性、行数和 SHA-256
校验，将源分片标为只读。恢复先写临时文件，再比较完整性、行数和 checksum 后原子替换。

Windows 计划任务默认工作日 16:10 执行，重复实例设为 `IgnoreNew`，失败最多重启 6 次、间隔
5 分钟。执行脚本显式将所有交易执行环境变量设为 disabled/false。
