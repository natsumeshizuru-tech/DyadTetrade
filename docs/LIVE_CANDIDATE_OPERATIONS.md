# DyadTetrade 实盘候选版本操作手册

更新时间：2026-07-13

## 1. 当前安全边界

- FastAPI 只承载工作台 API；QMT 连接、回调、委托和撤单只存在于独立 Trading Engine。
- Trading Engine 固定为单进程、单 worker、无 reload，并由 `logs/runtime/trading.pid` 和 QMT 网关文件锁共同约束。
- 默认执行模式为 `disabled`；专用启动脚本显式进入 `qmt_sim`，且
  `DYADTETRADE_ENABLE_LIVE_TRADING=false`。
- `qmt_live` 需要每日门禁、账户白名单和人工确认；候选版本验收不发送真实资金委托。
- `tick_momentum_small_cap` 继续为 Paper/影子策略，`can_send_live_orders=false`。

## 2. 一键启动与停止

先启动并登录 miniQMT 模拟客户端，然后在项目根目录执行：

```powershell
.\scripts\start_trading_workbench.ps1 -ExecutionMode qmt_sim
```

脚本依次启动 `Trading Engine :8011`、`FastAPI :8000` 和 `Vite :5173`，三者都就绪后再执行开盘前检查。禁止添加 `--reload` 或多个 worker。

```powershell
.\scripts\preopen_check.ps1
.\scripts\stop_trading_workbench.ps1
```

前端入口：`http://127.0.0.1:5173`。一级导航固定为交易、行情、策略、研究、系统。

## 3. 五态风险状态机

| 状态 | 新开仓 | 减仓 | 撤单 | 用途 |
|---|---:|---:|---:|---|
| NORMAL | 允许 | 允许 | 允许 | 已连接且完成对账 |
| REDUCE_ONLY | 禁止 | 允许 | 允许 | 仅降低已有风险 |
| CANCEL_ONLY | 禁止 | 禁止 | 允许 | 清理活动委托 |
| HALTED | 禁止 | 禁止 | 受运维流程控制 | Kill Switch |
| RECOVERY | 禁止 | 禁止 | 受运维流程控制 | 启动、断线或崩溃后的强制对账 |

`HALTED` 只能先进入 `RECOVERY`；`RECOVERY → NORMAL` 必须成功查询资产、持仓、委托和成交并完成 OMS 对账。风险状态持久化在 SQLite，重启不能绕过。

每次进入 `RECOVERY` 都会清空上一轮 `reconciled_at`，旧对账成功时间不能作为本轮恢复资格。
`HALTED`、`CANCEL_ONLY`、`REDUCE_ONLY` 在 qmt_sim 重启并完成对账后恢复原状态，不自动变为
`NORMAL`。qmt_sim 可在这些状态启动诊断界面，但风控继续拒绝不允许的订单；qmt_live 仍要求
`NORMAL` 才能通过发布门禁。

## 4. 全天操作阶段

| 阶段 | 时间（北京时间） | 操作 |
|---|---|---|
| PREOPEN | 08:30–09:30 | 启动、迁移检查、连接、数据检查、对账；不主动下单 |
| TRADING_AM | 09:30–11:30 | 监控行情新鲜度、订单、策略心跳和风险 |
| LUNCH | 11:30–13:00 | 午间对账；不主动新开仓 |
| TRADING_PM | 13:00–15:00 | 同上午交易监控 |
| CLOSING | 15:00–15:15 | 停止新委托，核对活动订单 |
| POSTCLOSE | 15:15–18:00 | 结算、报告、数据增量更新、审计和备份 |
| CLOSED | 其余及周末 | 保持安全停机或 RECOVERY |

系统 API `GET /api/v1/monitoring/market-session` 返回当前阶段和建议动作。

## 5. 订单与恢复流程

```text
浏览器二次确认
→ FastAPI 代理
→ Trading Engine Token 校验
→ 五态风控 + 白名单 + 金额/频率/行情/账户检查
→ order_stock_async
→ qmt_seq
→ 异步响应映射 qmt_order_id
→ 委托/成交回调进入事件队列
→ OMS 持久化
→ 前端轮询/WebSocket 展示
```

客户端订单 ID 和幂等键在进程重启后仍从 OMS 恢复。重复回调忽略；乱序回调保留更高成交量和终态；
成交与撤单竞争时 `FILLED` 优先，撤单失败恢复对应活动状态。数据库短时锁最多重试三次，超过阈值
进入可定位失败。快速重连和账户健康恢复均保留 recovery latch，不能绕过新对账。

断线或进程异常后：进入 `RECOVERY` → 禁止新委托 → 重连 → 查询柜台四类快照 → OMS 对账 →
核对本地和券商活动订单集合 → 人工检查 → 回到原受限状态或 `NORMAL`。本地活动订单在券商查询中
缺失、券商活动订单无法关联本地 OMS、账户不健康，或本地终态/成交量被券商旧快照倒退时，对账
失败关闭到 `HALTED` 并写
`qmt_reconciliation_mismatch`。禁止直接从 `HALTED` 跳到 `NORMAL`。

## 6. 开盘前与收盘后命令

```powershell
# 全量质量检查
.\scripts\check_all.ps1

# 开盘前只读与安全检查（工作台已启动）
.\scripts\preopen_check.ps1

# 收盘后日线增量更新
.\scripts\postclose_daily_update.ps1
```

依赖环境拆分：

```powershell
python -m pip install -r requirements/trading.txt
python -m pip install -r requirements/research.txt
python -m pip install -r requirements/dev.txt
```

Trading Engine 导入边界由 `scripts/check_trading_imports.py` 验证，不允许加载 Torch、Transformers、CatBoost、LightGBM 或 XGBoost。

## 7. 极小资金实盘前仍需通过的门禁

1. 至少连续多个交易日 QMT 模拟柜台异步委托、成交/撤单、断线恢复和重启对账无差异。
2. 人工复核券商账号白名单、委托上限、日上限、标的白名单、行情新鲜度和 Kill Switch。
3. 完成真实账户只读资产/持仓/订单/成交核对，但不下单。
4. 建立独立告警通道、操作人员在场和券商应急撤单方案。
5. 将首次真实资金发布限制为一个账号、一个白名单标的、一手，并在每单前人工确认。
6. 任一检查未通过，保持 `qmt_live` 拒绝启动。
