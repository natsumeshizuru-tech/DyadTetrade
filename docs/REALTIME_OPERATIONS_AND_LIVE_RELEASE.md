# DyadTetrade 实时监控与交易发布手册

更新时间：2026-07-13

## 1. 当前默认运行方式

默认仍使用 miniQMT 模拟账户：

```powershell
.\scripts\start_trading_workbench.ps1
```

前端地址为 `http://127.0.0.1:5173`。启动脚本使用单个 Uvicorn worker，不使用 `--reload`，
并持有单交易网关进程锁。

## 2. 实时计算口径

实时服务默认只处理：

- `600519.SH`、`510300.SH` 等显式自选；
- 从沪深主板目录筛选出的 50 只低流通市值候选；
- 最多 500 个显式配置标的。

不会订阅全市场 Tick。XtData 快照每 2 秒轮询一次，相同时间、价格、成交量和成交额的 Tick
会被去重。

Tick 生成的当日日 K 线使用：

- 未复权价格；
- 当日第一条可见 Tick/open 字段作为开盘价；
- 累计最高、最低和最新价；
- XtData 累计成交量和成交额；
- `source=xtdata-tick-daily-simulation`。

模拟日 K 只用于策略信号和监控，委托风控仍使用最新未复权 Tick 和订单限价，禁止直接使用
复权价格构造委托。

实时因子包括 Tick 动量、日内收益、流通市值、动量排名、小市值排名和 70%/30% 组合评分。
所有结果带当前可见时间。闭市快照会显示 `market_fresh=false`，并被下单风控拒绝。

## 3. Tick 动量加小市值策略

内置策略名为 `tick_momentum_small_cap`：

1. 只接受上海 `600/601/603/605` 和深圳 `000/001/002/003` 主板代码；
2. 排除名称包含 ST、没有正流通股本或没有正参考价的标的；
3. 先按实时流通市值选最小的 20 只；
4. 再按可见 Tick 动量选择前 5 只且动量不低于阈值；
5. Paper/影子模式最大总仓位 25%，单标的目标权重上限 5%，入选标的等权后再取较小值；
6. 默认至少累计 3000 个候选 Tick 才再次调仓，避免每个轮询批次重复换仓；
6. 当前按钮只启动 Paper 运行器，不具备实盘订单能力。

启动入口位于前端“策略监控”，或调用：

```text
POST /api/v1/realtime/strategies/tick-momentum-small-cap/start
```

策略状态、参数、最近心跳、订单数、成交数、记录数和错误信息在：

```text
GET /api/v1/monitoring/strategies
```

## 4. 账户监控

前端“账户监控”显示实际 QMT 查询结果：

- 连接、订阅、心跳和最近对账；
- 总资产、可用资金、现金比例、持仓市值和总敞口；
- 持仓成本、未实现盈亏和持仓权重；
- 委托状态统计、活动委托、成交金额；
- Kill Switch、只撤单、禁止新开仓；
- 最近风控事件。

账号只返回脱敏值，userdata 路径不会进入 API、前端或结构化日志。

## 5. 收盘后日线更新

每个交易日 15:10 后运行：

```powershell
.\scripts\postclose_daily_update.ps1
```

脚本读取 SQLite 中当前活跃股票、ETF 和指数，创建最近 10 天的幂等日线增量任务，继续执行
`raw → clean → trusted → bt_ready` 和质量报告。15:10 前会安全拒绝，不下载全市场历史 Tick。

## 6. qmt_live 每日发布门禁

真实交易不会写入 `.env` 示例或自动发现。启动前必须在当前 PowerShell 会话中显式提供：

```powershell
$env:DYADTETRADE_QMT_ACCOUNT_ID = "<真实账号>"
$env:DYADTETRADE_QMT_USERDATA_PATH = "<真实QMT userdata_mini>"
$env:DYADTETRADE_QMT_LIVE_ACCOUNT_WHITELIST = "<同一真实账号>"
$env:DYADTETRADE_ALLOWED_SYMBOLS = "<逐只确认的白名单>"
$env:DYADTETRADE_MAX_ORDER_NOTIONAL = "<单笔上限>"
$env:DYADTETRADE_MAX_DAILY_NOTIONAL = "<单日上限>"
$env:DYADTETRADE_MAX_SYMBOL_POSITION_NOTIONAL = "<单标的上限>"
$env:DYADTETRADE_MAX_ORDERS_PER_MINUTE = "<每分钟订单上限>"
```

`qmt_live` 不使用脚本默认值：以上四个限额必须在当日启动的 PowerShell 会话中逐项显式设置，
且单笔上限不得高于单日上限。`qmt_sim` 才会在缺省时使用 10000/50000/20000/5 的保守默认值。

然后运行：

```powershell
.\scripts\start_trading_workbench.ps1 -ExecutionMode qmt_live
```

脚本会要求人工输入 `I_CONFIRM_QMT_LIVE`，确认只在当天有效。缺少任一配置、账号不匹配、日期
不匹配、Kill Switch 触发或账号未订阅时启动失败。

当日已接受委托金额、幂等键和最近一分钟委托时间会在网关重启时从 SQLite 订单及 QMT 成交
恢复，按 Asia/Shanghai 交易日计算；重启不能清空单日金额和频率限制。

此外，每笔真实委托 API 仍需同时传入：

```json
{
  "confirm_live_order": true,
  "live_confirmation": "I_CONFIRM_QMT_LIVE_ORDER"
}
```

前端只有在执行模式确实为 `qmt_live` 时才显示真实委托区域，并要求逐单输入完整确认短语；
确认内容不持久化。实盘首次发布应先完成只读资产、持仓、委托、成交和对账检查，再以单账号、
单标的、一手限价委托进行人工验收。

## 7. 主要状态接口

| 接口 | 用途 |
|---|---|
| `GET /api/v1/realtime/status` | Tick、模拟日K、因子和行情新鲜度 |
| `PUT /api/v1/realtime/watchlist` | 设置受控实时标的，最多 500 个 |
| `POST /api/v1/realtime/mainboard-small-cap/refresh` | 重建主板小市值候选 |
| `GET /api/v1/monitoring/strategies` | 所有策略运行详情 |
| `GET /api/v1/monitoring/account` | 账户资金、持仓、订单、成交和风控 |
| `GET /api/v1/execution/release-gate` | 实盘发布门禁检查，不暴露账号 |
| `GET /api/v1/ws/monitor` | 账户、策略、行情和实时计算状态流 |

## 8. 明确限制

- 当前 Tick 来源是有界快照轮询，不是逐笔全市场推送。
- Tick 动量小市值策略仅完成 Paper/Dry-run 运行，不自动连接真实账户。
- 开盘前只有真实账号持有人才能完成 qmt_live 账号白名单和双确认。
- 未经人工确认不发送真实委托，也不会把模拟结果称为实盘验证。
