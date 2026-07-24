# DyadTetrade 开盘前 MVP 运行手册

更新时间：2026-07-12

> 已在实盘候选版本收敛前归档，仅作为历史快照。

## 安全边界

本版本只允许 `qmt_sim` 模拟账户。启动时同时满足以下条件才会建立交易连接：

```text
DYADTETRADE_EXECUTION_MODE=qmt_sim
DYADTETRADE_ENABLE_QMT_SIM_TRADING=true
DYADTETRADE_ENABLE_LIVE_TRADING=false
```

默认启动仍保持 `qmt_sim` 和 `ENABLE_LIVE_TRADING=false`。`qmt_live` 已改为严格的每日发布
门禁；缺少真实账号白名单、当日人工确认、标的白名单或限额时会在 lifespan 阶段失败。完整
账号和 `userdata_mini` 路径不会返回前端或写入结构化日志。实盘发布操作见
[`REALTIME_OPERATIONS_AND_LIVE_RELEASE.md`](REALTIME_OPERATIONS_AND_LIVE_RELEASE.md)。

## 一条命令启动

先启动并登录国金 QMT 模拟客户端，然后执行：

```powershell
cd <path-to-DyadTetrade>
.\scripts\start_trading_workbench.ps1
```

脚本只启动一个 Uvicorn worker，不使用 `--reload`。它会从明确标记为“模拟”的 QMT 快捷方式
发现 `userdata_mini`，且只接受恰好一个模拟账户目录；发现结果只进入当前进程环境，不写入文档
或前端。默认委托白名单是 `510300.SH`，可在启动前用环境变量覆盖。

启动成功后：

- 前端：`http://127.0.0.1:5173`
- API：`http://127.0.0.1:8000`
- Swagger：`http://127.0.0.1:8000/docs`

停止：

```powershell
.\scripts\stop_trading_workbench.ps1
```

## 开盘检查

```powershell
.\scripts\preopen_check.ps1
```

检查项包括 miniQMT 进程、XtQuant 导入、XtData 连接、Alembic、最近交易日日线、执行模式、
实盘开关、账号订阅、Kill Switch、前后端、WebSocket 和数据覆盖。

日线预检由 `scripts/preopen_data_check.py` 执行。XtData 本地交易日历在收盘后可能比已经落盘的
行情短暂滞后一天，因此只有 `trusted` 最新日期早于日历最新日期才失败；数据日期更新于本地
日历不会被误判为陈旧。

## 模拟账户只读冒烟

以下命令只连接、订阅和查询，不发委托：

```powershell
.\.venv\Scripts\python.exe scripts\qmt_sim_smoke_test.py
```

如果不满足 `qmt_sim` 配置会安全失败。

## 首笔模拟委托

只在 A 股交易时段、实时行情新鲜、模拟账号正常、Kill Switch 未触发时操作。前端“账户监控”
页面要求：

1. 使用白名单标的；
2. 数量固定为一手 100；
3. 输入限价；
4. 勾选“确认一手模拟委托”；
5. 人工点击提交。

命令行也要求双参数确认：

```powershell
.\.venv\Scripts\python.exe scripts\qmt_sim_smoke_test.py `
  --confirm-sim-order --confirm-text I_CONFIRM_QMT_SIM `
  --symbol 510300.SH --price <人工确认限价>
```

不开启确认参数时真实调用 `order_stock_async()` 的代码路径不会执行。当前风控默认单笔不超过
10,000 元、单日不超过 50,000 元，并检查账号、标的、行情时间、网关、重复键、每分钟订单数、
单标的持仓、全局/账户/策略停止开关、只撤单和禁止新开仓。

## 策略编辑与发布

策略页面支持：新建模板、编辑、保存草稿、AST/compile 校验、60 Bar 小样本回测、发布、启动、
停止和心跳。文件只能位于 `user_strategies/*.py`；旧草稿进入 `.backups`，发布快照进入
`.published/<name>/vN.py`。

校验会拒绝路径穿越、非 Python 文件、语法错误、未继承 `Strategy`，以及直接导入 XtQuant、
SQLite、SQLAlchemy、DuckDB、socket 或 subprocess。未通过校验和小样本回测不能发布；未发布
版本不能启动。策略验证运行在独立子进程，具备心跳、超时和停止。

## 数据状态

旧全市场来源以只读方式导入：5,537 个 Parquet、8,311,239 行，源覆盖 2019-01-02 至
2026-06-16；导入错误为 0。随后用 miniQMT 执行增量补齐，不重下已有多年 A 股历史。

数据任务和失败原因在数据中心页面、`/api/v1/market/summary`、下载任务 API 以及
`reports/data_quality/` 中可见。启动不会等待后台批量更新结束。

`600519.SH` 和 `510300.SH` 已保存近期 `1m` 数据，行情页可在“日线/分钟线”之间切换，并按需
读取 miniQMT 实时快照。

## 已验证结果

- Alembic：`20260712_10 (head)`，无模型漂移。
- 开发前基线：75 个 pytest，82% 覆盖率。
- 最新检查：92 个 pytest 已通过，覆盖率 82%。
- miniQMT XtData：连接成功，6,972 个板块。
- XtTrader 模拟账户：连接和订阅成功；只读查询得到资产、16 个持仓、0 个委托、0 个成交。
- 重启：账户和 16 个持仓重新对账并持久化。
- 策略：创建、校验、60 Bar 回测、发布 v1、子进程心跳和停止通过。
- 浏览器：`600519.SH`、策略编辑器、qmt_sim 页面和系统监控通过。
- Kill Switch：实际 API 拒绝新委托，前后 QMT 委托数均为 0。
- 全市场日线：7,701/7,779 个活跃标的已有 trusted/bt_ready，覆盖率 99.00%；
  7,676 个覆盖到 2026-07-10。
- 四层日线：每层 7,701 个标的、约 10,674,731 行；bt_ready 因严格去重少 2 行。
- 分钟线：`600519.SH`、`510300.SH` 每层共 3,856 行。
- 严格失败：78 个目录标的没有可用 trusted 数据；任务 4/5 共记录 99 个下载错误，主要为
  空行情、零价或负成交额，均未用未来值或伪造行情填充。
- 实盘：始终为 false，没有向真实资金账户发送委托。

## 尚待开盘时人工完成

周末/闭市期间实时行情时间戳是陈旧的，风控会按设计拒绝委托。首笔 `qmt_sim` 一手限价单、异步
响应、委托回报和撤单必须在下次交易时段由用户人工确认后执行，不能由自动验收代替该确认。
