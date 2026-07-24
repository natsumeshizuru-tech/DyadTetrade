# MiniQMT 多策略管理与监控参考审查

更新日期：2026-07-13  
用途：记录本轮 GitHub 检索、静态安全审查、隔离验证及 DyadTetrade 的落地边界。

## 1. 结论

本轮没有把任何第三方项目作为 DyadTetrade 运行依赖，也没有启动第三方交易程序、登录器、
行情订阅或下单接口。最终采用的是“阅读并重写小型设计模式”的方式：

- 从 cytrade 借鉴策略实例 ID、心跳、启动恢复、暂停/停止、运行快照及 REST 状态聚合；
- 从 EasyXT 借鉴多策略列表、状态、日志和进程角色的界面信息组织；
- 继续沿用 quant-qmt-proxy 的会话隔离、回调入队和网关边界；
- 继续沿用 vn.py 的事件驱动、应用生命周期和网关职责隔离；
- OSkhQuant 仅用于理解本地可视化和策略日志展示，不采用其运行代码。

DyadTetrade 的策略仍不能直接调用 XtTrader。监控 API 对每个策略固定返回
`can_send_live_orders=false` 和 `live_order_policy=blocked`；策略产生订单意图后仍必须经过
风险检查、OMS 和独立交易网关。`qmt_live` 默认关闭。

## 2. 下载与版本固定

| 项目 | 来源 | 本地版本 | 许可证 | 本地目录 | 处理 |
|---|---|---|---|---|---|
| cytrade | `YangSal/cytrade` | `7884b3e2a71d69fe14bd645c5b130983d2bbcd37` | MIT | `references/miniqmt_projects/cytrade` | 新下载、采用设计模式 |
| EasyXT | `quant-king299/EasyXT` | `eba642ba20d620834b8883e64ccc2b0fedaac341` | MIT | `references/miniqmt_projects/EasyXT` | 新下载、仅采用经验证的 UI 组织 |
| OSkhQuant | `khscience/OSkhQuant` | 既有 `main` 归档，未固定提交 | CC BY-NC 4.0 | `references/miniqmt_projects/OSkhQuant` | 仅阅读，不复制代码 |
| quant-qmt-proxy | `liqimore/quant-qmt-proxy` | 既有 `master` 归档，未固定提交 | MIT | `references/miniqmt_projects/quant-qmt-proxy` | 既有边界设计参考 |
| vn.py | `vnpy/vnpy` | 既有 `master` 归档，未固定提交 | MIT | `references/miniqmt_projects/vnpy` | 既有事件设计参考 |

新下载项目的原始 ZIP 保留在 `references/miniqmt_projects/_archives/`，文件名包含审核提交哈希。
第三方目录只读参考，不加入 Python 路径，不参与 DyadTetrade 启动。

## 3. 安全审查

### cytrade

可借鉴部分：`strategy/base.py`、`strategy/runner.py`、`monitor/watchdog.py`、
`web/backend/routes.py` 和 `web/backend/websocket.py` 中的运行状态、心跳、恢复、快照和状态广播。

拒绝接入部分：真实异步下单/撤单、账号密码配置、钉钉 Webhook 与签名密钥、外部 HTTP 推送。
隔离冒烟时 Webhook 为空，未发生网络调用。

### EasyXT

可借鉴部分：`gui_app/widgets/multi_strategy_widget.py` 的多策略列表、状态、日志和进程角色展示。

拒绝接入部分：自动登录、读取 `.env` 账号/Token、真实下单与撤单、直接启动外部交易进程。
该项目存在上游代码质量问题，不能作为运行依赖。

### 共同拒绝项

- 不复制或运行真实委托、撤单、自动登录和账户凭据读取路径；
- 不配置第三方 Webhook，不上传源代码、行情、日志或账户信息；
- 不允许第三方策略绕过 DyadTetrade 风控、OMS、执行模式和进程锁；
- 不记录完整账户 ID、Token、密码或 `userdata_mini` 私有路径；
- 不让回调线程运行策略或执行复杂数据库操作。

## 4. 隔离验证结果

| 检查 | 结果 | 说明 |
|---|---|---|
| cytrade 全量 `compileall` | 通过 | 全部 Python 文件可编译 |
| cytrade Watchdog 无网络冒烟 | 通过 | 健康心跳、121 秒超时、无连接/无订阅安全分支通过 |
| OSkhQuant 全量 `compileall` | 通过 | 有一个无效转义 `SyntaxWarning`，未影响编译 |
| EasyXT 多策略组件 `py_compile` | 通过 | 监控组件本身可编译 |
| EasyXT 全量 `compileall` | 失败 | 3 个工具脚本存在 SyntaxError/IndentationError |
| 上游自动化测试 | 不存在 | 三个候选目录均未发现测试文件，不能声称上游测试套件通过 |

EasyXT 失败文件：

- `tools/download_all_stocks.py:114`：SyntaxError；
- `tools/download_market_cap_fast.py:146`：IndentationError；
- `tools/setup_duckdb.py:370`：IndentationError。

因此本轮“测试通过”的范围仅包括：cytrade/OSkhQuant 编译、cytrade 安全心跳冒烟、EasyXT
监控组件单文件编译，以及 DyadTetrade 自身新增功能的完整测试；不把 EasyXT 描述成整体通过。

## 5. DyadTetrade 落地设计

### 5.1 运行状态

`GET /api/v1/monitoring/strategies` 返回全部持久化策略实例，并合并当前进程中的 Paper Runner。
统一健康状态：

| 健康状态 | 判定 |
|---|---|
| `healthy` | 运行中且心跳年龄不超过 15 秒 |
| `stale` | 运行中且心跳年龄超过 15 秒 |
| `waiting` | 运行中但尚无心跳 |
| `error` | 状态为 failed/error/crashed 或存在运行错误 |
| `stopped` | stopped/completed/cancelled |

每个实例显示模式、运行状态、启动/停止时间、运行时长、最后心跳、最后活动、参数、状态、
委托、活动委托、成交、风控事件、恢复状态和进程角色。汇总同时返回总数、运行、健康、超时、
异常、停止、等待数量和按模式计数。

### 5.2 恢复语义

| 恢复状态 | 含义 |
|---|---|
| `attached` | 持久化实例已与当前内存 Paper Runner 对接 |
| `awaiting_recovery` | 持久化状态仍运行但当前没有对应 Runner，需要恢复或人工检查 |
| `not_required` | 已停止，无需恢复 |

本状态只用于监控，不能自动越过发布门禁或触发委托。

### 5.3 策略页面

“策略”一级页面默认进入“运行监控”，每 5 秒读取真实 API：

- 同时列出多个策略实例；
- 按策略名/实例 ID 搜索；
- 按健康状态和运行模式筛选；
- 选择实例查看参数、状态、日志、委托/成交/风控统计和恢复状态；
- 汇总运行、健康、超时、异常和停止数量；
- 明示策略无 XtTrader 直连能力；
- 编辑、校验、冒烟、发布及影子启动保留在“代码编辑”工具中。

## 6. 实盘准备边界

本轮增强的是“可观察性”和“恢复诊断”，不是放开实盘。进入极小资金实盘前仍必须通过：

1. 策略发布版本和参数快照固定；
2. Paper 或 qmt_sim 连续运行且心跳无超时；
3. 委托只经过 `OrderIntent -> Risk -> OMS -> Gateway`；
4. QMT 模拟柜台异步响应、回调、撤单和重启对账实机通过；
5. NORMAL/REDUCE_ONLY/CANCEL_ONLY/HALTED/RECOVERY 风险状态正确；
6. Kill Switch、账号白名单、标的白名单和资金上限有效；
7. 人工发布 `qmt_live`，默认配置仍安全拒绝；
8. `tick_momentum_small_cap` 继续保持 Paper/影子模式。

## 7. 本轮验收证据

- `scripts/check_all.ps1`：通过；161 个 pytest 全部通过，覆盖率 80%；Ruff、Ruff format、
  strict mypy、交易依赖边界、Alembic 差异检查、前端 5 个 Vitest、ESLint、TypeScript 和生产
  构建全部通过；
- miniQMT 只读状态：XtData 已连接，板块数量 6972；
- qmt_sim 启动门禁：Trading Engine 单进程、模拟账号订阅、账户健康、风险状态 NORMAL；
- 实际监控 API：2 个持久化策略实例，其中 1 个运行且健康、1 个已停止；运行实例恢复状态为
  `attached`，实盘委托能力为 `false`；
- 浏览器：多策略列表和详情切换通过，控制台错误为 0；截图保存在
  `reports/validation/strategy-monitor-browser.png`；
- 启动、测试和浏览器验收全程保持 `qmt_live=false`，未发送任何委托。
