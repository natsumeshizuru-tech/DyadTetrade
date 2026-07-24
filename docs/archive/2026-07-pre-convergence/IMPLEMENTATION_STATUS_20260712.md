# DyadTetrade 阶段实施与验收记录

更新时间：2026-07-12

> 已在实盘候选版本收敛前归档，仅作为历史快照。

> 本文件已按 2026-07-12 最终磁盘状态刷新。完整技术手册见
> [`DYADTETRADE_TECHNICAL_MANUAL.md`](DYADTETRADE_TECHNICAL_MANUAL.md)。

## 本轮落地范围

本轮在原有行情数据中心上增量完成了以下可运行链路：

1. 厂商无关领域模型、订单状态机和同步/异步事件总线。
2. 策略 SDK、策略注册发现、四个示例策略和可持久化因子研究工作台。
3. A 股事件驱动回测、任务幂等、数据指纹、结果持久化和取消状态。
4. 绩效指标以及 JSON、CSV、HTML、PDF、SVG 报告。
5. 网格、随机、Optuna、多目标/Pareto 搜索，以及稳健性、Walk-forward 和 Monte Carlo 检验。
6. 带约束的组合配置、再平衡计划、Paper Trading、OMS、交易前/交易中风控、状态恢复和对账。
7. XtData/XtTrader 对象转换边界，以及仅允许 miniQMT 模拟账户的执行网关、回调队列、重连和对账。
8. SQLAlchemy 业务表、十次 Alembic 增量迁移、FastAPI API/WebSocket 和 React 开盘工作台。
9. 结构化日志、请求 ID、Prometheus 指标、WebSocket 监控、就绪检查和统一检查脚本。

任何真实发送入口均未启用；`DYADTETRADE_ENABLE_LIVE_TRADING=false` 是启动和验收前提。

## 主要新增文件

- `src/dyadtetrade/domain/{enums,models,orders}.py`
- `src/dyadtetrade/events/bus.py`
- `src/dyadtetrade/modules/strategy/{base,examples,registry}.py`
- `src/dyadtetrade/modules/research/factors.py`
- `src/dyadtetrade/modules/backtest/{accounting,broker,clock,commission,constraints,data_feed,engine,matcher,repository,result,schemas,service}.py`
- `src/dyadtetrade/modules/performance/analytics.py`
- `src/dyadtetrade/modules/reporting/reporter.py`
- `src/dyadtetrade/modules/optimization/engine.py`
- `src/dyadtetrade/modules/portfolio/allocator.py`
- `src/dyadtetrade/modules/oms/service.py`
- `src/dyadtetrade/modules/risk/engine.py`
- `src/dyadtetrade/modules/paper_trading/{repository,service}.py`
- `src/dyadtetrade/integrations/{xtquant_converters,xttrader_gateway}.py`
- `src/dyadtetrade/api/routes/{backtest,optimization,paper_trading,portfolio,research,strategy}.py`
- `src/dyadtetrade/core/observability.py`
- `alembic/env.py`、`alembic/script.py.mako` 和 `alembic/versions/20260712_*.py`
- `scripts/{check_all.ps1,validate_workbench.py}`
- `frontend/src/{App.test.tsx,test/setup.ts}`
- `docs/{QUANT_WORKBENCH.md,IMPLEMENTATION_STATUS_20260712.md}`
- `tests/test_{backtest_engine,backtest_service,domain_events,optimization_portfolio_paper,paper_repository,performance_reporting,strategy_research,workbench_api,xtquant_converters}.py`

## 主要修改文件

- `README.md`、`pyproject.toml`、`alembic.ini`
- `src/dyadtetrade/api/main.py`
- `src/dyadtetrade/api/routes/{system,market_data}.py`
- `src/dyadtetrade/core/{config,logging}.py`
- `src/dyadtetrade/db/{models,repositories,session}.py`
- `src/dyadtetrade/integrations/xtquant.py`
- `src/dyadtetrade/modules/{registry}.py`
- `src/dyadtetrade/modules/market_data/tasks.py`
- `frontend/{package.json,package-lock.json,vite.config.ts}`
- `frontend/src/{App.tsx,styles.css,api/client.ts}`
- `Dockerfile`、`docker-compose.yml`
- 原有测试中的系统、下载任务和 API 回归用例。

没有删除历史数据、旧报告、参考项目或既有 API。由于当前目录不是 Git 工作树，以上清单按本轮实施记录归类，而不是由 Git diff 推导。

## 数据库迁移

- `20260712_01`：研究、策略、回测、优化、组合、Paper、OMS、风控与审计表。
- `20260712_02`：补齐证券行业和指数字段索引。
- `20260712_03`：因子定义、运行血缘和结果字段。
- `20260712_04`：可恢复优化任务。
- `20260712_05`：板块和股票池时点快照。
- `20260712_06`：策略运行状态。
- `20260712_07`：回测基准、相对净值和超额结果。
- `20260712_08` 至 `20260712_10`：策略文件生命周期、QMT 模拟订单映射及索引对齐。
- 当前版本：`20260712_10 (head)`。
- 当前数据库共 33 个业务/元数据表；行情时序仍存储在 Parquet，并通过 DuckDB 查询。
- 自动 downgrade 不执行破坏性删表；历史 SQLite 和 Parquet 均未删除。

## 实际场景结果

数据区间为 2026-05-06 至 2026-07-10：

- 10 只 A 股共 429 条 `bt_ready` 日线。
- 4 只 ETF 共 188 条 `bt_ready` 日线。
- `600519.SH` 买入持有、`510300.SH` 双均线和 10 股轮动任务均成功并持久化。
- ETF 组合生成 4 条订单意图，目标权重合计 0.8，未发送券商。
- 基础因子 369 个有效观察，覆盖率 1.0，Rank IC 均值 0.076016，ICIR 0.167489。
- 多目标优化完成 6 个试验、2 个 Pareto 点、6 个稳健性情景、2 个 Walk-forward 折和 100 次 Monte Carlo；系统保留多重检验告警，没有只输出“最优参数”。
- miniQMT 全推快照 `510300.SH=4.829` 成功转换为领域 Tick，并驱动 Paper 订单成交；真实委托数为 0。
- `600519.SH` 小数据集期末资产：自研引擎、Backtrader、向量化核算均为 `1,001,862.42`，差异为 0。

机器结果见 `reports/validation/workbench_validation.json`。

## 最终检查结果

- 环境导入：78/78；`pip check` 通过。
- Alembic upgrade/current/check 通过，无待生成迁移。
- Ruff 和 Ruff format 通过。
- strict mypy：100 个源文件通过。
- pytest：92 个测试通过，覆盖率 82%。
- 前端 Vitest：1 个组件流程测试通过。
- ESLint、TypeScript 编译和 Vite 生产构建通过。
- FastAPI 启动、健康和 API 冒烟通过。
- miniQMT 只读连接成功，读取到 6972 个板块。
- 实盘 fail-closed 检查通过。

## 运行命令

```powershell
# 全部自动检查
.\scripts\check_all.ps1

# 真实短区间闭环（只读行情 + Paper）
.venv\Scripts\python.exe scripts\validate_workbench.py

# 数据库迁移
.venv\Scripts\alembic.exe upgrade head
.venv\Scripts\alembic.exe check

# 后端
.venv\Scripts\python.exe -m uvicorn dyadtetrade.api.main:app --reload

# 前端
cd frontend
npm run dev
```

## 尚未完成或需要继续强化

- 逐 Tick 撮合、盘口队列位置和算法执行模型。
- ST/板块/品种规则的逐日历史版本，以及分红拆股进入会计引擎。
- 完整历史成分股快照；当前静态股票池不能用于避免长周期幸存者偏差。
- 优化任务的并行 worker、RDB Study 恢复、完整 Walk-forward、Purged K-fold、Embargo 和 Monte Carlo 套件。
- 行业约束、风险预算和换手约束的统一 CVXPY 求解模型。
- 报告 HTML/SVG/PDF 首页已使用统一交易日横轴输出 K 线、成交标记、净值、可选基准和回撤；前端 K 线与回测图仍为分区查看，后续可进一步合并交互联动。
- 多 Paper 账户、跨进程锁、自动交易日结算和长时间断线故障注入。
- miniQMT 模拟账户的连接、订阅和只读查询已实际联调。异步模拟委托、回调、撤单和重启后订单对账
  已由 mock 自动化测试覆盖；首笔真实模拟账户委托仍需在交易时段由人工确认价格后执行。
- 前端 ECharts 已动态加载；主包约 256.55 kB，图表包约 609.07 kB，图表分包仍有体积警告。

这些限制不影响当前“本地数据 → 研究/回测 → 报告 → 前端查看 → 优化 → Paper/OMS/风控”的安全闭环，但在进入任何真实交易开发前必须继续补齐。
