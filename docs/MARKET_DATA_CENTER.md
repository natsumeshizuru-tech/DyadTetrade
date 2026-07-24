# DyadTetrade 行情数据中心与股票池

更新时间：2026-07-12

## 目标与安全边界

本模块是模块化单体中的基础能力，负责 miniQMT/XtData 只读行情、证券目录、板块、
股票池、数据流水线、质量检查与研究查询。XtData 调用在 `XtQuantGateway` 内由同一把
可重入锁串行化，避免 FastAPI 线程池并发进入非线程安全的券商扩展。

`DYADTETRADE_ENABLE_LIVE_TRADING=false` 是硬边界。本模块没有导入 XtTrader、账户、
持仓、委托或撤单接口。

## 数据流

```text
miniQMT/XtData cache
        |
DownloadTask -> DownloadTaskItem (逐标的重试、恢复、隔离)
        |
raw -> clean -> trusted -> bt_ready
 |        |         |          |
原始表格  标准字段   质量有效行   稳定回测输入
        |
SQLite 元数据 + CSV/JSON 质量报告
        |
DuckDB bars_trusted / bars_bt_ready 视图
        |
FastAPI -> React 数据中心
```

Parquet 使用按层、周期和证券代码组织的稳定路径；写入先生成临时文件再原子替换。
重复下载会按 `datetime + symbol` 合并去重，因此任务重跑和安全重叠下载是幂等的。

## 下载任务状态机

任务状态：`pending -> running -> succeeded | partial | failed`。

- 幂等键由数据源、排序后的标的、周期、复权方式和起止日期计算 SHA256。
- 每个 `DownloadTaskItem` 独立记录状态、尝试次数、行数和最后错误。
- 重试采用指数退避；某个标的永久失败后继续处理其余标的。
- 已成功任务重复创建时返回原任务，不重复下载。
- 已有 trusted 文件时，从最新日期向前重叠 7 个自然日续传，再由 Parquet 去重。
- 应用启动会把异常中断的 `running` 任务和任务项恢复为 `pending`。
- `run-pending` worker 可在重启后继续处理；失败项可用 `run-task --retry-failed` 重置。

## 标准字段

四层最终统一使用：

```text
datetime, symbol, open, high, low, close, volume, amount,
pre_close, suspended, adjust_factor, period, adjust
```

- 证券代码统一为 `000001.SZ`、`600519.SH`、`430047.BJ`。
- 时间统一为无时区 `datetime64`；日线为交易日零点。
- `suspended` 优先使用来源字段，否则按成交量和成交额同时为零推断。
- `adjust` 记录 XtData 实际请求的复权模式。
- 当前券商日线未直接返回逐日复权因子，`adjust_factor` 使用相对基准 `1.0`；真实
  复权因子下载将在公司行动数据阶段补充。
- `pre_close` 从当前可用序列前一行推导，下载区间第一行可能为空。

## 质量规则

每个任务生成 `reports/data_quality/download_task_<id>.json` 和同名 CSV，并将问题写入
SQLite：

- 空数据；
- 必要字段缺失和 OHLC 空值；
- 重复交易时间；
- `high/low/open/close` 关系异常或价格非正；
- 负成交量或负成交额；
- 交易日历缺口；
- 最新目标交易日未覆盖。

缺少交易日是 warning，因为可能由停牌、上市前或退市后造成；非法 OHLC、负成交量和
空数据是 error。`trusted` 与 `bt_ready` 仅保留通过行级硬校验的数据。

## SQLite 元数据

- `instruments`：A 股、ETF、指数详情、行业、上市/退市日期、价格步长和交易单位。
- `sectors` / `sector_members`：基础板块、一级行业和常用指数成分。
- `trading_days`：市场交易日历。
- `data_files`：层级、覆盖起止、行数、大小、SHA256、流水线版本与导入来源。
- `download_tasks` / `download_task_items`：批次和逐标的进度。
- `quality_issues`：可查询的质量问题。
- `stock_pools` / `stock_pool_members`：自定义或动态股票池。

启动时执行加法式 SQLite 升级，只增加新列和表，不删除旧数据。一次错误板块同步产生的
非目标证券也不会物理删除，而会标记为 inactive；默认 API、覆盖率和股票池只使用 active
目录。

## 股票池

股票池支持：

- 显式自定义代码；
- SH/SZ/BJ 市场；
- stock/ETF/index 类型；
- 一级行业；
- 任意已持久化板块；
- 常用指数成分；
- 证券代码前缀；
- 上述条件组合的动态池。

首次运行 `sync-catalog --refresh-sectors` 可下载券商行业板块缓存。为控制规模，只持久化
基础市场板块、THY1/GICS1/SW1/CSRC1 一级行业和沪深300、中证500、中证1000、
上证50成分板块，避免把数千个等权衍生板块全部写入本地元数据。

## 运维命令

```powershell
# 后端与前端
python -m uvicorn dyadtetrade.api.main:app --reload
cd frontend; npm run dev

# 目录同步
python scripts/market_data.py sync-catalog --refresh-sectors
python scripts/market_data.py sync-catalog --skip-details

# 下载和恢复
python scripts/market_data.py create-task --symbols '@symbols.txt' `
  --start 20100101 --end 20260710 --period 1d --adjust front --run
python scripts/market_data.py run-task 1 --retry-failed
python scripts/market_data.py run-pending --limit 20

# 查询与旧数据导入
python scripts/market_data.py query --symbols 600519.SH,510300.SH `
  --start 20260101 --end 20260710 --layer trusted --limit 500
python scripts/market_data.py import '<path-to-old-daily-data>' --period 1d --adjust front
```

旧数据导入器递归读取 CSV 和 Parquet，支持常见中文字段名、按文件名或 symbol 字段识别
证券代码；只读取来源，所有输出写入 DyadTetrade 数据目录，并在 `data_files.imported_from`
保留来源绝对路径。
