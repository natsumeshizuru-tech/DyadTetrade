# Codex Skills Audit

审计日期：2026-07-12  
范围：DyadTetrade 项目级 Skills、当前用户级 Skills、系统兼容目录及候选第三方仓库。

## 结论

- 启用 27 个 Skill：项目级 26 个，用户级 1 个。
- 其中项目自建 16 个，经过静态审查的第三方 11 个。
- 未发现重复名称、缺少 `SKILL.md`、失效的本地引用或已启用但无目录的 Skill。
- 所有项目交易相关 Skill 均以 `ENABLE_LIVE_TRADING=false`、无真实委托、无真实账号为硬边界。
- VectorBT 上游 Skills 未直接安装；其印度市场、OpenAlgo、`.env` 和安装脚本假设被替换为四个项目级安全 Skill。
- 本轮不能安全重启 Codex Desktop；文件系统发现和验证已经完成，新 Skills 需在下一任务或重启后进入会话级发现列表。

## 环境

| 项目 | 状态 |
|---|---|
| OS | Windows NT 10.0.26200 |
| Shell | Windows PowerShell 5.1.26100.8655 |
| Python | 3.12.0 |
| Node.js / npm | v24.15.0 / 11.12.1 |
| Git | 2.54.0.windows.1 |
| GitHub CLI | 未安装 |
| Codex CLI 版本 | 当前桌面沙箱执行 `codex.exe --version` 被系统拒绝，未伪造版本号 |
| 仓库状态 | 当前目录不是 Git 仓库 |

## 搜索路径检查

| 路径 | 检查结果 |
|---|---|
| 当前目录到工作区根的 `.agents/skills/` | 原先不存在；现已创建项目目录 |
| `$REPO_ROOT/.agents/skills/` | 现有 26 个项目级 Skill |
| `$HOME/.agents/skills/` | 原先不存在；现有 `webapp-testing` |
| `/etc/codex/skills/` | Windows 对应位置不存在；只检查，未修改 |
| `$HOME/.codex/skills/` | 仅记录为当前系统内置 Skill 的兼容目录；没有向这里安装项目或用户 Skill |

当前 Codex 会话明确从 `$HOME/.codex/skills/.system/` 提供 `skill-creator` 与
`skill-installer`，因此该路径只作为系统兼容事实记录。项目安装遵循当前 `.agents/skills/`
约定。

## 安装取舍

### 通用工程

- 安装 `webapp-testing`：Anthropic 原始 Skill，仅含 Playwright 工作流和本地服务生命周期脚本。
- 暂缓 `gh-fix-ci`、`gh-address-comments`：官方候选安全，但本机缺少 `gh` 且当前目录不是 Git 仓库。
- 拒绝 `brooks-lint`：候选仓库不是单一同名 Skill，包含 hooks、安装脚本、外部 API 配置和
  `curl | bash` 安装说明；同时与 Ruff、mypy、pytest、ESLint 高度重叠。
- 不安装 `codebase-recon`：内容未发现高风险写操作，但依赖 Git 历史和 POSIX 命令，当前项目与
  PowerShell 环境不满足其有效使用条件。
- 不安装 `mcp-builder`：当前项目没有开发 MCP 服务的明确需求。
- 不重复安装系统内置 `skill-creator`、规划类或同名 Skill。

### 量化研究

- 从 `agiprolabs/claude-trading-skills` 保留 `backtrader`、`portfolio-analytics`、
  `cointegration-analysis`，并加入 DyadTetrade 本地数据、A 股口径和禁止下单覆盖规则。
- 其余候选因加密市场默认、Birdeye/Jupiter/API Key、实时外部调用、自动交易倾向、内容为
  stub，或与项目自建 Skill 严重重叠而不安装。
- 中国股票研究仓库的七个 Skill 全部安装；修复扁平安装后的共享引用路径，并加入公开信息研究、
  禁止券商/账户/委托调用的规则。
- VectorBT 四个上游 Skill 未直接安装，改为四个边界更窄的项目 Skill。A 股市场参数位于
  `config/market_rules.a-share.json`，费用字段默认留空并要求复核，避免把过期规则当事实。

## 项目资料核对

已读取或抽取：XtQuant 总览 Markdown/DOCX、XtData 与 XtTrader 校正版 DOCX、完整实例、常见问题、
QMT Python API PDF、算法/网格/智能算法资料，以及因子研究资料。校正版文档中确认存在
`userdata_mini`、`download_history_data`、`get_market_data`、`subscribe_quote`、
`XtQuantTrader`、`order_stock` 和 `cancel_order` 等主题。

用户点名的 `miniQMT文档与代码示例.txt` 与 `backtrader技术文档.txt` 未在知识库中找到；本轮使用
现有同类校正版资料、项目技术文档和已安装 Backtrader 包补足，没有制造不存在的来源。

## 验证摘要

- 官方 `quick_validate.py`：27/27 通过（Windows 下设置 `PYTHONUTF8=1`）。
- 项目 `scripts/validate_skills.py`：27 个 Skill，0 重复，0 缺失引用，16 个自建 Skill 均有
  2 个正向和 2 个负向触发测试。
- 依赖环境：79 个分发包已安装，78 个导入成功，0 缺失、0 导入失败。
- MiniQMT：xtquant 已安装、行情连接成功、板块数 6972、实盘关闭。
- 最小闭环：读取本地 `600519.SH` 的 5 条 `bt_ready` 日线；重复 0、关键字段空值 0、OHLC
  异常 0、负成交量 0；Backtrader 离线模拟完成，未导入 XtTrader。
- 项目工程检查：Ruff 通过；严格 mypy 通过（32 个源码文件）；pytest 21 项通过；前端 ESLint
  通过；Vite 生产构建通过。构建仅有主 bundle 大于 500 kB 的性能警告。
- `webapp-testing` 依赖已补齐：项目虚拟环境安装 Playwright 1.61.0，本地 Chromium
  149.0.7827.55 已成功无头启动。没有全局安装软件。
- 负向安全运行：把 `DYADTETRADE_ENABLE_LIVE_TRADING=true` 传给最小闭环时，程序在读取/回测
  前以非零状态安全失败。

详细来源、提交、许可证和路径见 `skills-manifest.json`；风险项见
`skills-security-review.md`。
