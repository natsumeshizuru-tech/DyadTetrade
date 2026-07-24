# DyadTetrade

给 MiniQMT 配套的 A 股行情、数据、交易和风控工作台。

MiniQMT 启动并登录后，DyadTetrade 可以通过 XtData 下载行情和基础数据，通过 XtTrader 对接模拟盘或受控实盘。没有 MiniQMT 时，也能使用 Paper 模式完成策略联调。公开版本默认关闭真实交易。

## 能做什么

| 模块 | 用途 |
| --- | --- |
| 行情 | 实时订阅、分钟聚合、断线恢复、心跳和连续性检查 |
| 数据 | 用 XtData 下载历史、财务、板块、合约、节假日、可转债、ETF 与指数权重；将 1d/1m 数据导入共享 SQLite |
| 策略 | 管理策略目录、执行股票池和来自 DyadResearch 的发布包 |
| 执行 | Paper、qmt_sim、qmt_live 三套路径；统一进入 OMS 和 Broker 接口 |
| 风控 | 订单、仓位、日内额度、频率、白名单、Kill Switch；可在前端调低上限并保留审计记录 |
| 运维 | 活动订单恢复、成交归属、账户账本、对账、监控和运行状态 |

## 快速开始

先用 Paper 模式确认前后端和数据库正常：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ".[trading,dev]"
$env:DYADTETRADE_EXECUTION_MODE = "paper"
$env:DYADTETRADE_ENABLE_LIVE_TRADING = "false"
.venv\Scripts\python -m alembic upgrade head
.venv\Scripts\python -m uvicorn dyadtetrade.api.main:app --host 127.0.0.1 --port 8000
```

API 文档位于 `http://127.0.0.1:8000/docs`。

启动前端：

```powershell
cd frontend
npm ci
npm run dev
```

## 接入 MiniQMT

1. 合法安装并登录 MiniQMT，确认客户端可以查看行情。
2. 复制本地配置：

   ```powershell
   Copy-Item config\market_data.local.example.yaml config\market_data.local.yaml
   Copy-Item .env.example .env
   ```

3. 在 `config/market_data.local.yaml` 中填写 `userdata_mini`、`datadir` 和 XtQuant `site-packages`。
4. 在 `.env` 中填写需要的账户类型和风控参数；第一次运行保持 `paper` 或 `qmt_sim`。
5. 启动应用，在“行情”页面运行 XtData 检查，再下载或导入数据。

本地配置、QMT 缓存、账户信息和数据库都被 Git 忽略。可下载的数据类别与限制见 [XTDATA_DATA_CATALOG.md](docs/XTDATA_DATA_CATALOG.md)。

## 和 DyadResearch 怎么配合

```text
MiniQMT / XtData
        │
        ▼
DyadTetrade  ── 下载并维护共享 SQLite
        │
        ▼ 只读
DyadResearch ── 因子、策略、回测、报告
        │
        ▼ StrategyReleaseBundle
DyadTetrade  ── 校验、风控、Paper / QMT 执行
```

[DyadResearch](https://github.com/natsumeshizuru-tech/DyadResearch) 不直接操作账户。研究结果只有导出为 `StrategyReleaseBundle`，并通过文件完整性、Schema、哈希、策略白名单和风险预算检查后，才会进入执行端。

## 交易边界

- 默认模式是 `disabled` 或 `paper`，真实交易开关默认关闭。
- qmt_sim 和 qmt_live 运行在独立进程中，SDK 和账户配置不随仓库分发。
- 订单先经过策略发布、执行股票池、幂等检查、风控和 OMS，再到 Broker。
- `paper_approved` 不能自动升级为实盘权限。
- Kill Switch、每日发布、白名单和人工确认不能由前端绕过。

本项目不是券商终端替代品，也不承诺任何收益。使用 qmt_live 前应准备当日发布、账户白名单、对账和回滚方案。

## 开发检查

```powershell
$env:DYADTETRADE_ENABLE_LIVE_TRADING = "false"
.venv\Scripts\python -m ruff check src tests
.venv\Scripts\python -m ruff format --check src tests
.venv\Scripts\python -m mypy -p dyadtetrade
.venv\Scripts\python -m pytest

cd frontend
npm run test
npm run lint
npm run build
```

交易状态和账本见 [TRADING_STATE_MACHINE.md](docs/TRADING_STATE_MACHINE.md)、[ORDER_LEDGER.md](docs/ORDER_LEDGER.md) 与 [PRECISION_AND_ACCOUNTING.md](docs/PRECISION_AND_ACCOUNTING.md)。实盘发布要求见 [FULL_MARKET_AUTOMATED_EXECUTION.md](docs/FULL_MARKET_AUTOMATED_EXECUTION.md)。许可证和安全说明见 [DEPENDENCY_LICENSES.md](DEPENDENCY_LICENSES.md)、[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 与 [SECURITY.md](SECURITY.md)。
