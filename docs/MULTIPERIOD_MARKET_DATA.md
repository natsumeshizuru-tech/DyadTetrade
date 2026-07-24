# 全市场多周期行情数据运行手册

更新日期：2026-07-13

## 数据口径

- 原生下载：`1d` 从 2010-01-01 开始，`1m` 和 `tick` 从 2024-01-01 开始。
- `tick` 明确按 MiniQMT Level1 快照处理，目标频率约 3 秒、完整交易日约 4,800 条/标的；
  不将其误标为逐笔成交或 Level2 数据。
- 确定性派生：`1m -> 5m/15m/30m/1h`，`1d -> 1w/1q/1y`。
- Bar 同时保存 `none`（真实成交价）和 `front`（前复权研究价）；Tick 不复权。
- Bar 继续走 `raw -> clean -> trusted -> bt_ready`。Tick 仅保存 vendor raw 与 canonical
  trusted，两层均使用 ZSTD Parquet，按交易年拆分下载任务，避免大任务失败后从 2024 年重跑。
- 时序数据写 Parquet，由 DuckDB 视图查询；SQLite 只保存下载任务、逐标的进度、文件指纹、
  覆盖日期和质量问题，不把海量行情逐行塞进 SQLite。

## 当前容量门禁结论

2026-07-13 只读规划统计到 5,558 只活跃股票，E 盘空闲 209.09 GiB。按 Level1/3 秒重新
估算后，2024 年至今 Tick 约为 16,540,608,000 行，raw + trusted 两层约 2.05 TiB；连同
双口径日线、分钟线和派生周期，完整方案约 2.75 TiB。3 秒频率对单只股票不大，但乘以
5,558 只股票、约 620 个交易日和两个物理层后仍然是十亿级行数，不能按单日样本判断容量。
因此当前磁盘不满足门禁，程序已生成计划但没有创建任务、更没有启动下载。这是 Critical
容量阻塞，不允许用 `force` 绕过。

规划证据：`reports/market_data/full_market_level1_3s_plan.json`。

## 交易日历

- MiniQMT 返回的毫秒时间戳先按 UTC 瞬时值解析，再转换为 `Asia/Shanghai`，避免中国周一
  被错误落到 UTC 周日。
- SQLite 保存 SH/SZ/BJ 交易日历；权威区间同步时会清理该区间内不再存在的旧日期。
- Tick 和 Bar 标准化会拒绝周末，提供交易日集合时还会拒绝节假日记录。
- 盘中状态 API 优先使用 SQLite 日历；当前日期未覆盖时只读查询 XtData，最后才使用工作日
  降级判断，并在前端显示 `calendar_source`。
- `scripts/preopen_check.ps1` 会先运行 `scripts/sync_trading_calendar.py`。2026-07-13 实机同步
  结果为 SH 4,011 日、SZ 4,011 日、BJ 2,257 日，数据库周末记录为 0。

## 安全命令

```powershell
# 只规划，不下载；必须显式加入 --include-tick 才会把 Tick 放入计划
python scripts/bulk_market_data.py plan --end 20260713 --batch-size 100 --include-tick `
  --output reports/market_data/full_market_level1_3s_plan.json

# 仅当计划中的 executable=true 时才创建 SQLite 下载任务；仍不会自动执行
python scripts/bulk_market_data.py create-native-tasks reports/market_data/bulk_plan.json

# 原生 1m/1d 完成后可恢复、幂等地派生；state 文件逐标的记录完成和失败
python scripts/bulk_market_data.py derive --period 15m --adjust none
python scripts/bulk_market_data.py derive --period 1q --adjust front

# none/front 二次复核：覆盖、成交量、同 Bar O/H/L/C 复权比例一致性
python scripts/bulk_market_data.py validate-adjustments --period 1d
```

下载仍由现有 `scripts/market_data.py run-pending` 驱动，支持逐标的隔离、指数退避、断点恢复、
幂等键和质量报告。建议先配置至少 4 TiB 可用空间的独立行情盘，再按 `1d -> 1m -> 派生 Bar -> Tick`
分阶段执行，并在每阶段复核容量与质量；不要在当前 E 盘直接启动全市场 Tick。
