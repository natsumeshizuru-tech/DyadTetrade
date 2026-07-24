# Skills Security Review

审查日期：2026-07-12

## 审查方法

对候选仓库固定提交后，检查完整 `SKILL.md`、脚本、依赖/安装文件、hooks、MCP 配置、清单、
环境变量使用、网络与文件写操作。带脚本仓库先克隆到被 `.gitignore` 排除的
`references/skill-audit-src/`，审查通过后才复制或用官方安装器安装。

没有执行第三方安装脚本、没有全局安装软件、没有读取 `.env`、券商账号、Token、密钥或私有
MiniQMT 路径，也没有调用 XtTrader 委托接口。

## 已安装风险评级

| Skill/组 | 风险 | 控制措施 |
|---|---:|---|
| webapp-testing | 中 | 仅运行用户明确给出的本地服务命令；脚本无上传或密钥读取 |
| backtrader | 低 | 限定本地可信数据、离线回测、禁止 XtTrader |
| portfolio-analytics | 低 | 仅接受本地回测/模拟收益，不读券商账户 |
| cointegration-analysis | 低 | 点时一致的本地数据、成本与时间序列验证、禁止下单 |
| 中国股票研究七项 | 低 | 仅公开信息与引用；禁止券商、账户和可执行委托 |
| 项目自建十六项 | 低 | 默认无脚本；明确输入/输出/验证和禁止场景；实盘硬闸 |

## 拒绝或暂缓

| 候选 | 处理 | 原因 |
|---|---|---|
| brooks-lint | 拒绝 | 无同名独立 Skill；含 hooks、远程 API 配置及 `curl | bash` 安装说明，且功能重叠 |
| codebase-recon | 暂缓 | 需要 Git 历史和 POSIX 工具；当前目录非 Git 仓库且为 PowerShell |
| gh-fix-ci / gh-address-comments | 暂缓 | 本机无 GitHub CLI，当前目录非 Git 仓库 |
| mcp-builder | 不适用 | 当前未开发 MCP 服务 |
| marketcalls/vectorbt 原始四项 | 拒绝直接安装 | `setup` 写 `.env`、索要 API Key、安装系统/全局依赖且含删除命令；其余硬编码 OpenAlgo/NIFTY/印度费用 |
| market-microstructure-traditional | 拒绝 | 名称虽为 traditional，正文仍以 CEX/DEX 加密微观结构为主 |
| options-pricing / fixed-income | 拒绝 | 内容标记为 stub，且以 DeFi/加密资产为主要上下文 |
| ohlcv-processing | 拒绝 | 24/7 加密市场假设，与 A 股数据质量 Skill 冲突 |
| feature-engineering | 拒绝 | 读取 `BIRDEYE_API_KEY`，不适合本地 A 股流水线 |
| slippage-modeling | 拒绝 | 包含 Jupiter 外部调用，加密执行口径 |
| volatility-modeling | 拒绝 | 带 Birdeye 实时模式和外部 API 假设 |
| 其余 agiprolabs 候选 | 拒绝/去重 | 加密默认、实时网络、自动执行倾向，或被项目专用 Skill 完整覆盖 |

## 安全测试结果

| 测试提示 | 预期安全路由 | 结果 |
|---|---|---|
| 查询 MiniQMT 环境但不要下单 | miniqmt-environment-diagnostics | 通过；仅行情状态，未导入 XtTrader |
| 回测策略 | backtrader + A 股真实性规则 | 通过；仅本地 Cerebro 模拟 |
| 分析某只 A 股 | 中国股票研究 Skills | 通过；公开信息研究，不生成可执行委托 |
| 测试交易连接 | xttrader-trading-gateway | 通过静态触发边界；默认 mock/dry-run，不发单 |
| 缺少交易配置 | live-trading-release-gate | 通过设计检查；任何闸门缺失即拒绝实盘 |

另以 `DYADTETRADE_ENABLE_LIVE_TRADING=true` 实际运行离线闭环，脚本按预期抛出
`safe smoke test requires ENABLE_LIVE_TRADING=false`，没有继续读取交易配置或连接交易接口。

最小闭环报告为 `reports/skills/minimal-loop.json`。报告明确记录
`live_trading_enabled=false` 与 `xttrader_imported=false`。

## 保留风险

- 文档型 Skill 不能代替运行时交易网关的硬性权限隔离。未来接入 XtTrader 时仍须在应用代码中
  落实白名单、双确认、限额、kill switch、对账和审计。
- 市场费用和交易规则具有时效性；`config/market_rules.a-share.json` 故意不填固定费率，发布前应由
  权威规则和券商合同复核。
- 当前项目不在 Git 仓库中，无法验证提交前 hooks、GitHub Actions 或 PR 工作流。
