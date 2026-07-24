# DyadTetrade 异机开发交接说明

本说明用于把当前 Windows 工作区搬到另一台电脑继续开发。交接包保留完整源码、未提交修改、
Git 历史、项目文档、测试、脚本、运行配置、数据、报告和参考资料；只排除可重建依赖与缓存。

## 交接基线

- 生成日期：2026-07-14
- Git 分支：`main`
- 最近提交：`2fbb5b9d7d0555f2af4d0f62ff960334039fb2cd`
- 工作区状态：包含未提交修改和未跟踪文件，这是有意保留的当前开发现场
- Python：3.12（项目约束 `>=3.12,<3.13`）
- Node.js：当前机器使用 v24.15.0
- npm：当前机器使用 11.12.1

## 交接包包含内容

- `DyadTetrade/`：项目完整工作区和 `.git` 历史
- 当前所有已修改但尚未提交的源码
- `data/`：行情分层数据、SQLite、备份、运行检查和隔离数据
- `references/`：MiniQMT 项目参考与审计参考源
- `reports/`：回测、数据质量、界面验收和工作流报告
- `.env` 与 `config/qmt_live.local.ps1`：当前机器本地配置
- `handoff_docs/Development.md`：持续开发提示词
- `handoff_docs/开发过程.md`：历史开发修改存档
- `handoff_docs/working-tree.patch`：已跟踪文件未提交修改的额外补丁备份
- `handoff_docs/DyadTetrade-history.bundle`：Git 完整历史的额外 bundle 备份

交接包包含 QMT 账号 ID、本机路径和风险参数，不应公开分享。上传个人网盘后，应开启网盘自身的
访问控制；在新电脑解压后应重新核对本地配置。

## 有意排除内容

以下内容体积大、与机器绑定或可自动重建，不进入交接包：

- `.venv/`
- 任意 `node_modules/`
- `frontend/dist/`
- `__pycache__/`、`*.pyc`、`*.pyo`
- `.pytest_cache/`、`.mypy_cache/`、`.ruff_cache/`、`.hypothesis/`
- `*.tsbuildinfo`、`.coverage`、`coverage.xml`
- 活动进程 PID、运行日志、锁文件、SQLite WAL/SHM 临时文件

## 新电脑恢复步骤

1. 将压缩包完整解压，避免只复制 Git 已跟踪文件。
2. 安装 64 位 Python 3.12、Git 和 Node.js；安装并登录与账号匹配的 MiniQMT/QMT 客户端。
3. 进入项目目录创建 Python 环境：

   ```powershell
   py -3.12 -m venv .venv
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   pip install -r requirements\dev.txt
   playwright install chromium
   ```

4. 安装前端依赖：

   ```powershell
   cd frontend
   npm ci
   cd ..
   ```

5. 修改 `.env` 中的数据路径、SQLite URL、XtQuant 路径；修改
   `config/qmt_live.local.ps1` 中的 `DYADTETRADE_QMT_USERDATA_PATH`，使其指向新电脑真实的
   `userdata_mini`。同时重新核对账号、白名单和全部风控上限。
6. 先关闭真实交易开关运行完整检查：

   ```powershell
   $env:DYADTETRADE_ENABLE_LIVE_TRADING = "false"
   $env:DYADTETRADE_ENABLE_QMT_LIVE_TRADING = "false"
   $env:DYADTETRADE_EXECUTION_MODE = "disabled"
   .\scripts\check_all.ps1
   ```

7. 启动普通开发工作台：

   ```powershell
   .\scripts\launch_dyadtetrade.ps1
   ```

8. 只有在新电脑的 QMT 客户端、账号、路径、执行池和风控全部重新验证后，才按
   `docs/LIVE_TRADING_RUNBOOK.md` 进入 QMT 流程。不要直接沿用旧电脑上的当日确认状态。

## 数据库说明

打包时工作台可能仍在运行。交接包中的 `data/sqlite/dyadtetrade.db` 使用 SQLite 在线备份 API
生成一致性快照，不依赖复制活动中的 WAL 文件。原有历史数据库备份也会保留。

## 完整性验证

压缩包同目录会生成 `.sha256` 文件。上传后和下载后分别执行：

```powershell
Get-FileHash .\DyadTetrade-Development-Handoff-*.tar.gz -Algorithm SHA256
```

结果必须与 `.sha256` 中记录的哈希完全一致；若不一致，不要在该副本上继续开发。
