# DyadTetrade 运行策略与实盘发布手册

更新时间：2026-07-14

## 1. 当前结论

当前版本可用于研究、回测、实时行情、Paper 策略和 miniQMT 模拟账户监控。`qmt_live` 具备
独立 Trading Engine、账户订阅、资产/持仓/委托/成交查询、OMS 对账、异步委托、撤单、
风险状态机和逐单确认代码路径，但尚未在本机真实资金账户上发送委托。

本机当前运行口径允许 `qmt_live` 连接 QMT 模拟资金账户，用于验收与真实资金相同的启动、门禁、
委托、回报和对账流程。此时必须设置 `DYADTETRADE_QMT_ACCOUNT_IS_SIMULATED=true`；账本同时记录
`execution_mode=qmt_live` 与 `is_simulated=true`，不得把该验证表述为真实资金实盘验证。

`tick_momentum_small_cap` 和用户策略进程仍为 Paper/影子模式，明确返回
`can_send_live_orders=false`。现阶段实盘发布仅允许人工确认的单笔 API 委托，不允许策略自动
发送真实委托。

## 2. 每日运行策略

### 开盘前 08:30–09:20

1. 启动并登录对应的 miniQMT 客户端，确认极简模式、账号和 `userdata_mini` 路径。
2. 在项目根目录运行 `scripts/check_all.ps1`。
3. 先以 `qmt_sim` 启动，运行 `scripts/preopen_check.ps1`。
4. 核对执行模式、QMT 连接/订阅、风险状态、行情新鲜度、资产、持仓、活动委托和前一日对账。
5. 核对当日白名单标的的 trusted 日线、实时快照和质量问题；不要求无关标的全部通过才能运行，
   但白名单标的必须通过。
6. Paper 策略至少观察一个完整信号周期；有重复拒单、陈旧行情、心跳异常或对账差异时不得发布。

### 交易中

- 只保持一个 Trading Engine、一个 QMT 网关、一个 worker；禁止 `--reload`。
- 监控 `NORMAL/REDUCE_ONLY/CANCEL_ONLY/HALTED/RECOVERY`、行情年龄、策略心跳、活动委托和成交。
- 行情超过 10 秒、网关断线、账号异常或进入非 NORMAL 状态时，不得新下单。
- 午间执行一次资产、持仓、委托和成交对账；不主动新增自动交易能力。

### 收盘后

1. 确认无遗留活动委托，核对 OMS 与柜台。
2. 导出交割单和审计记录。
3. 运行 `scripts/postclose_daily_update.ps1` 更新日线与质量报告。
4. 停止工作台并备份 SQLite 元数据、配置快照、日志和报告。

## 3. 当前 Paper 策略

`tick_momentum_small_cap` 仅选择沪深主板股票，版本为 1.2.0：

- 小市值候选 20 只，动量排序后最多选择 5 只；
- 最大总仓位 25%；
- 单标的目标权重最多 5%；
- 默认至少累计 3000 个候选 Tick 才再次调仓；
- 卖出数量受 A 股 T+1 可卖数量约束；
- 同一时间只恢复或运行一个实例；
- 只进入 Paper Broker，不进入 QMT 真实网关。

启动和检查：

```powershell
Invoke-RestMethod -Method Post `
  http://127.0.0.1:8000/api/v1/realtime/strategies/tick-momentum-small-cap/start

Invoke-RestMethod http://127.0.0.1:8000/api/v1/monitoring/strategies
Invoke-RestMethod http://127.0.0.1:8000/api/v1/paper/snapshot
```

## 4. 模拟账户标准启动

```powershell
cd <project-root>\DyadTetrade
.\scripts\start_trading_workbench.ps1 -ExecutionMode qmt_sim
.\scripts\preopen_check.ps1
```

浏览器打开 `http://127.0.0.1:5173`。停止使用：

```powershell
.\scripts\stop_trading_workbench.ps1
```

## 5. 实盘发布前置门禁

本机 QMT 模拟资金的 `qmt_live` 账号、路径、白名单和风险限制保存在被 Git 忽略的
`config/qmt_live.local.ps1`，使用以下入口加载：

```powershell
.\scripts\start_qmt_live.ps1
```

脚本仍要求人工输入当天的 `I_CONFIRM_QMT_LIVE`，不会绕过每日发布门禁。

在真实账号 PowerShell 会话中显式设置，不写入仓库或日志：

```powershell
$env:DYADTETRADE_QMT_ACCOUNT_ID = "<真实股票账号>"
$env:DYADTETRADE_QMT_USERDATA_PATH = "<真实QMT的userdata_mini目录>"
$env:DYADTETRADE_QMT_LIVE_ACCOUNT_WHITELIST = "<与账号完全相同>"
$env:DYADTETRADE_ALLOWED_SYMBOLS = "<首次仅一个人工确认标的>"
$env:DYADTETRADE_MAX_ORDER_NOTIONAL = "5000"
$env:DYADTETRADE_MAX_DAILY_NOTIONAL = "5000"
$env:DYADTETRADE_MAX_SYMBOL_POSITION_NOTIONAL = "5000"
$env:DYADTETRADE_MAX_ORDERS_PER_MINUTE = "1"
```

以上金额只是首次技术验收的保守上限示例，不构成投资建议。脚本不会为 `qmt_live` 补默认值；
缺少任一项、数值非正、单笔上限高于单日上限、账号不匹配或路径不存在时都拒绝启动。

## 6. 启动 qmt_live 的只读阶段

1. 先停止模拟工作台。
2. 启动并登录配置对应的 QMT 客户端，确认函数交易权限和账号一致。
3. 使用本机模拟资金配置入口，或在同一个 PowerShell 会话显式设置上一节真实账号变量。
4. 执行：

```powershell
.\scripts\start_trading_workbench.ps1 -ExecutionMode qmt_live
```

5. 人工输入每日确认短语 `I_CONFIRM_QMT_LIVE`。
6. 启动后先只查询，不下单：

```powershell
$status = Invoke-RestMethod http://127.0.0.1:8000/api/v1/execution/status
$release = Invoke-RestMethod http://127.0.0.1:8000/api/v1/execution/release-gate
$account = Invoke-RestMethod http://127.0.0.1:8000/api/v1/monitoring/account

$status | Select-Object execution_mode,engine_online,connected,subscribed,account_healthy,risk_state
$release | ConvertTo-Json -Depth 4
$account.connection | ConvertTo-Json -Depth 3
```

必须同时满足：`qmt_live`、引擎在线、连接成功、账号订阅成功、账号健康、风险状态 NORMAL、
`release.ready=true`，且本地资产/持仓/委托/成交与 QMT 客户端人工核对一致。

## 7. 首笔极小资金人工委托

当前前端故意不提供 qmt_live 下单控件。只有完成真实账户只读核对后，才允许操作人员使用 API
提交一个白名单标的、一手、限价委托。每笔请求必须重新生成客户端订单 ID 和幂等键，并同时
提供逐单确认字段：

```powershell
$body = @{
  symbol = "<白名单标的>"
  direction = "buy"
  quantity = 100
  limit_price = "<人工核对的限价>"
  strategy_id = "manual-live-acceptance"
  client_order_id = [Guid]::NewGuid().ToString("N")
  idempotency_key = [Guid]::NewGuid().ToString("N")
  confirm_simulated_order = $false
  confirm_live_order = $true
  live_confirmation = "I_CONFIRM_QMT_LIVE_ORDER"
} | ConvertTo-Json

Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/execution/orders `
  -ContentType application/json -Body $body
```

提交后立即在前端及以下接口核对异步响应、`qmt_seq`、QMT order ID、订单状态和 OMS：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/execution/status
Invoke-RestMethod http://127.0.0.1:8000/api/v1/monitoring/account
```

如需撤销可撤委托，必须人工取得正确的 QMT order ID：

```powershell
Invoke-RestMethod -Method Post `
  "http://127.0.0.1:8000/api/v1/execution/orders/<QMT_ORDER_ID>/cancel?confirm_live_cancel=true"
```

## 8. 紧急停止和恢复

Kill Switch：

```powershell
$body = @{state="HALTED";reason="operator emergency halt";actor="operator"} | ConvertTo-Json
Invoke-RestMethod -Method Put `
  http://127.0.0.1:8000/api/v1/execution/risk-state `
  -ContentType application/json -Body $body
```

`HALTED` 后不得直接恢复交易。先进入 RECOVERY、完成四类快照和 OMS 对账，再调用：

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/v1/execution/reconcile
```

确认回到 NORMAL 前不得提交新委托。程序停止：

```powershell
.\scripts\stop_trading_workbench.ps1
```

关闭启动 PowerShell 后，当日环境变量和确认短语随会话消失；下一交易日必须重新发布。

## 9. 当前仍未放开的能力

- 不允许 `tick_momentum_small_cap` 或用户策略自动进入真实资金网关。
- 不允许多账号、多标的、多个策略同时做首次实盘验收。
- 不允许跳过模拟账户连续交易日验证、真实账号只读对账或人工逐单确认。
- 全市场仍有少量空行情、异常 OHLC/成交量和最新日覆盖缺口；任何实盘白名单标的必须单独验证。

## 10. QMT 账户资金与持仓口径

- 可用资金只认 `query_stock_asset(account).cash`，不得从该字段再次扣除 `frozen_cash`。
- 冻结资金、持仓市值和总资产分别读取 QMT 的 `frozen_cash`、`market_value` 和
  `total_asset`；配置中的金额只作为风险上限，不能替代账户事实。
- 下单风控前必须重新查询资产和持仓。买单使用最新可用资金；卖单使用对应持仓的
  `can_use_volume`。查询失败时保持失败关闭，不使用上一次缓存继续报单。
- `query_stock_orders()` 只覆盖当日委托。对本地已知活动委托还应按订单编号调用
  `query_stock_order()` 补查；若仍查不到，不得未经审计直接猜测终态或解除 `HALTED`。
