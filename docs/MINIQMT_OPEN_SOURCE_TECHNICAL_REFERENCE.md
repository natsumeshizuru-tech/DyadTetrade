# DyadTetrade miniQMT 开源项目技术参考

更新日期：2026-07-11  
测试平台：Windows、Python 3.12、券商 miniQMT 已启动并登录  
目标：为 DyadTetrade 的行情数据、QMT 网关、回测、执行和前端架构提供经本地验证的参考。

## 1. 结论摘要

不建议复制某一个项目作为 DyadTetrade 的整体底座。更适合组合参考：

| DyadTetrade 能力 | 首选参考 | 原因 |
|---|---|---|
| QMT 服务层、Mock 和权限隔离 | `quant-qmt-proxy` | 107项测试全部通过，REST、WS、gRPC、认证和Mock边界清晰 |
| XtData 并发安全 | `qmt-bridge` | 使用ASGI中间件串行化XtData调用，交易模块默认关闭 |
| 全市场采集、补缺和任务编排 | `ashares_data_collect` | 47项测试通过，具备DAG、重试、补缺、PostgreSQL和Parquet分层 |
| 策略、回测和券商抽象 | `bullet-trade` | 441项离线测试通过，DataProvider、Broker和QMT适配较完整 |
| 领域对象和事件模型 | `vnpy` | Tick、Bar、Order、Trade、Position、Account和Gateway模型成熟 |
| 桌面产品交互 | `OSkhQuant` | 数据下载、参数配置、回测结果展示流程直观 |
| 轻量实盘执行 | `lite-qmt-executor` | 信号、策略插件、订单执行、WAL恢复边界明确 |
| 跨机器访问QMT | `qmt-bridge` / `xqshare` | 前者是标准HTTP/WS；后者接近透明调用但采用GPLv3 |

对当前 DyadTetrade 最重要的结论：

1. 当前 FastAPI 使用线程池调用 XtData，但多个请求仍可能并发进入底层扩展。应增加全局串行化锁、单线程队列或独立QMT进程。
2. 历史数据下载应从同步请求升级为持久化任务：幂等键、状态机、重试、覆盖率和查漏补缺。
3. 前端只能连接 DyadTetrade API，不能直接依赖 XtQuant。
4. 数据服务和交易服务必须分离；行情默认只读，交易必须显式启用并经过权限、风控和审计。
5. Star数不能替代测试。多个高关注仓库存在测试漂移、依赖上界缺失或SDK过时问题。

## 2. 来源与本地目录

调研依据为用户分享的“量化软件功能简介”聊天：

https://chatgpt.com/g/g-p-69b6a19133d88191981382c231003c4e-liang-hua-jin-rong/shared/c/6a51f7bd-5830-83ea-aa71-db6700bf3d42

共下载13个仓库，保存在：

```text
DyadTetrade/references/miniqmt_projects/
  _archives/          GitHub默认分支ZIP
  _test_env/          独立测试环境
  bullet-trade/       OSKhQuant/          quant-qmt-proxy/
  lite-qmt-executor/  vnpy_qmt/           vnpy/
  ashares_data_collect/ qmt-bridge/        xqshare/
  miniqmt-demo/       easytrader/          QMT-MCP/
  XtQuant/
```

命令行 GitHub 传输当时持续超时，因此使用 GitHub 默认分支 ZIP。源码完整，但不包含提交历史；文末提供归档 SHA256。

## 3. 测试与安全边界

### 隔离原则

- 第三方依赖仅安装在 `references/miniqmt_projects/_test_env`。
- 没有把第三方项目安装进 DyadTetrade 主虚拟环境。
- 测试环境只读引用本机券商 XtQuant SDK。
- 13个仓库均先通过 Python 3.12 `compileall`；QMT-MCP有一处无效转义告警。

### miniQMT 验证范围

只执行板块、股票池、交易日和历史行情读取，以及单个标的的短区间日线下载。没有连接资金账户、查询个人资产、下单、撤单或启动自动交易。

因此，下文“通过”表示离线测试或只读行情链路通过，不代表真实资金交易通过。

## 4. 测试矩阵

| 项目 | 许可证 | 本地结果 | 判断 |
|---|---|---|---|
| BulletTrade | MIT | 441通过、1失败；真实日线8行、交易日8天 | 架构价值高；1项报告HTML断言脆弱，4个测试文件依赖仓库外策略文件 |
| OSkhQuant | CC BY-NC 4.0 | 编译通过；核心、风险、交易、数据查看器和GUI模块导入通过 | UI参考价值高；不能直接用于商业软件代码 |
| quant-qmt-proxy | MIT | 107/107通过 | QMT网关、Mock、协议和权限体系首选参考 |
| lite-qmt-executor | MIT | 核心执行、HTTP、WS和默认策略导入通过；只读QMT通过 | 适合执行模块；无自带测试，不能认定实盘可靠 |
| vnpy_qmt | Apache-2.0 | 与vn.py 4.4.0联合导入通过；Gateway和XtData只读通过 | 适合学习Gateway映射；测试缺失 |
| vn.py | MIT | 101通过、4失败 | 领域模型成熟；Alpha101有4处`cast_to_int`未注册 |
| ashares_data_collect | MIT | 47通过、1跳过；读取5203个代码和1行真实日线 | 数据工程首选；默认股票池不含北交所 |
| QMT Bridge | MIT | 健康、板块、交易日API均为200；33板块、8交易日 | 并发安全和只读/交易开关值得借鉴 |
| XQShare | GPL-3.0 | 可运行子集120通过、1失败；完整套件另有2个收集错误 | 思路可学；测试与实现漂移且许可证有传染性 |
| miniqmt-demo | 无LICENSE文件 | 20个Notebook、58个代码单元语法通过；只读行情通过 | 接口实验室；不复制代码 |
| easytrader | MIT | 7通过、26跳过；MiniqmtTrader安全实例化 | 券商抽象可参考；依赖文件严重陈旧 |
| QMT-MCP | 无LICENSE文件 | FastMCP工具注册通过；真实行情8行 | 仅研究AI层；交易安全边界不足 |
| ai4trade/XtQuant | MIT | 源码编译通过；仓库SDK在Python 3.12导入失败 | 只读文档和历史思路，不使用自带二进制 |

## 5. 分项目分析

### 5.1 BulletTrade

重点学习 `bullet_trade/data`、`broker`、`core`、`live`、`reporting` 和 `tests`：

- DataProvider与Broker分离，策略可复用于回测、模拟、本地QMT和远程QMT。
- MiniQMT Provider支持关闭自动下载，只读已有缓存，适合可复现回测。
- QMT断线冷却、订单等待、手续费、滑点、调度和服务适配有较多测试。

问题：默认Provider在导入时加载JQData，缺少`jqdatasdk`会导致大量测试无法收集；4个测试文件引用公开仓库之外的`strategies/bt_strategies`；报告测试断言Plotly生成HTML的精确字符串，且`plotly>=5.15`没有主版本上界。

DyadTetrade应学习接口抽象和测试组织，不复制其全局兼容层与导入时初始化方式。

### 5.2 quant-qmt-proxy

它把 REST、WebSocket、gRPC、服务层、模型层和QMT适配层分开，并提供Mock运行时、认证、健康检查和接口清单测试。107项测试全部通过，是此次最健康的miniQMT专用工程。

缺陷：测试结束后，少量gRPC后台线程继续向已关闭的Loguru捕获流写日志，出现`I/O operation on closed file`，后台线程和日志生命周期仍需收紧。

### 5.3 QMT Bridge

`src/qmt_bridge/server/app.py`明确指出XtData的C扩展非线程安全，并通过`XtdataSerializerMiddleware`串行化`/api/*`调用；定时下载拆成独立进程；交易模块仅在`trading_enabled=true`时连接。

这是对现有 DyadTetrade 最直接的改进：

```text
FastAPI -> XtData串行化中间件/调用队列 -> 单例Gateway -> XtData
```

Uvicorn应固定单worker。若Web层需要多进程，应把QMT Gateway拆成独立单进程服务。

### 5.4 ashares_data_collect

值得学习：任务标准接口、DAG、批次下载、自动重试、数据库查漏补缺、PostgreSQL结构化数据和Parquet Tick冷存。

问题：`get_a_share_codes()`一旦“沪深A股”有数据就不会合并北交所。本机得到5203个代码，而DyadTetrade当前沪深京过滤得到5558个，应保留自己的股票池合并逻辑。

### 5.5 vn.py 与 vnpy_qmt

适合采用的领域对象：Tick、Bar、Order、Trade、Position、Account、Contract、SubscribeRequest、OrderRequest、CancelRequest。

推荐边界：

```text
XtQuant原始对象 -> XtQuantGateway映射 -> DyadTetrade Domain -> EventBus -> OMS/Portfolio/Risk/API
```

不要让XtQuant对象直接穿透到策略、数据库和前端。当前4个失败属于Alpha101表达式注册问题，不是miniQMT问题。

### 5.6 OSkhQuant

适合参考miniQMT路径设置、数据/股票池选择、费用滑点配置、回测工作流，以及K线、成交点、净值和回撤展示。

不建议复制：顶层GUI脚本较集中、无自动化测试，且CC BY-NC 4.0禁止商业使用。DyadTetrade若未来封装或销售，只能参考交互思想。

### 5.7 lite-qmt-executor

其执行链为：外部信号 -> HTTP/WS -> TradingEngine -> Buy/Sell Strategy -> Broker -> XtQuantTrader。WAL恢复、防重复买入、撤单重挂、心跳和通知抽象值得参考。

但仓库没有测试，配置中含默认本机路径和账户占位值。DyadTetrade应先完成订单幂等、审计和模拟盘，再开发实盘执行。

### 5.8 XQShare

使用RPyC实现接近透明的远程XtQuant调用，认证、HMAC、TLS、权限、心跳和重连有参考价值。但测试仍导入实现中已不存在或改名的`CallbackServer`和`CallbackManager`，另有`xttrader`属性断言失败，说明测试落后于代码。

许可证为GPLv3；若DyadTetrade不采用GPLv3，不应复制或派生其代码。

### 5.9 miniqmt-demo

覆盖XtData、交易日、历史/实时行情、财务、ETF和XtTrader。所有58个代码单元可解析。

本机发现`get_trading_calendar()`因券商客户端不支持假期控制接口而抛出RuntimeError；回退`get_trading_dates()`后得到8个交易日。DyadTetrade当前多级日历回退是必要的。

### 5.10 easytrader

多券商统一接口和`MiniqmtTrader`包装可参考，但`requirements.txt`固定到2018年前后的Pandas、NumPy、Flask等版本，而`setup.py`又使用宽松依赖，不能作为新工程环境基线。

### 5.11 QMT-MCP

行情连接和FastMCP注册均可运行，但不适合作为核心底座：没有测试和LICENSE文件；配置带非空账户标识默认值；MCP直接暴露下单/撤单，缺少独立实盘开关、人工确认、账户白名单和双重授权；导入主模块即构造交易相关对象。

未来AI层只能产生交易意图或目标仓位，必须经过风险、权限和人工审批，不能直接获得券商下单能力。

### 5.12 ai4trade/XtQuant

行情服务、批量缓存和ClickHouse转存思路可读，但仓库二进制只包含CPython 3.6-3.8扩展，Python 3.12导入时报：

```text
ModuleNotFoundError: No module named 'xtquant.IPythonApiClient'
```

示例Sanic服务还使用4个worker调用XtData，与后续项目的非线程安全结论冲突。不能使用仓库内SDK或照搬服务配置。

## 6. 许可证决策

| 类别 | 项目 | 建议 |
|---|---|---|
| MIT等宽松许可 | BulletTrade、quant-qmt-proxy、lite-qmt-executor、vn.py、ashares_data_collect、qmt-bridge、easytrader、ai4trade/XtQuant | 满足署名后可复用少量代码，仍优先重新实现接口 |
| Apache-2.0 | vnpy_qmt | 可参考，复用时保留许可证和NOTICE要求 |
| 非商业许可 | OSkhQuant | 只参考交互，不复制进可能商业化的软件 |
| GPLv3 | XQShare | 不采用GPLv3时只学习思想 |
| 无许可证文件 | miniqmt-demo、QMT-MCP | 默认保留全部权利，不复制代码 |

## 7. 当前数据中心的下一阶段差距

### P0：XtData并发安全

- 全局异步锁或单线程调用队列。
- 所有XtData路由走同一串行化边界。
- Uvicorn固定单worker。
- 长下载任务与在线查询分进程。

### P0：下载任务闭环

- 真正使用现有`DownloadTask`表。
- 状态：pending、running、succeeded、partial、failed、cancelled。
- 幂等键：数据源、标的、周期、复权、起止日期。
- 重试、退避、超时、错误分类、进度和失败标的重跑。

### P1：覆盖率与补缺

- 记录每个标的最早/最晚日期、行数、更新时间。
- 依据交易日历检查缺失，区分停牌、未上市、退市和真实缺失。
- 提供全市场覆盖率报告。

### P1：Parquet分区与血缘

建议由单文件逐步升级为：

```text
trusted/bars_1d/trade_year=2026/symbol=600519.SH/part-*.parquet
```

保留原始版本、清洗规则版本和数据血缘。

### P1/P2：扩展数据和实时行情

- 分钟线、Tick、复权因子、财务、板块历史、指数权重、ETF和可转债。
- WebSocket订阅去重、引用计数、断线重订阅、Tick转Bar和前端限频。

## 8. 推荐目标架构

```text
React / TypeScript
        | HTTP / WebSocket
FastAPI Application
  |-- Market Data / Research / Backtest / OMS / Risk API
Application Services
  |-- DownloadTask / MarketQuery / Backtest / Order Service
Domain
  |-- Instrument / Bar / Tick / Order / Trade / Position / Account
        |                         |
Single-process QMT Gateway       Storage
  |-- XtData queue/lock          |-- Parquet / DuckDB
  |-- XtTrader default off       |-- SQLite/PostgreSQL
miniQMT
```

交易链必须是：

```text
Strategy -> Signal/TargetPosition -> Portfolio -> Risk -> ExecutionPlan -> OMS -> XtTraderGateway
```

策略不得直接调用XtQuantTrader。

## 9. 推荐实施顺序

1. 参考QMT Bridge，为现有Gateway增加串行化保护和单worker约束。
2. 参考ashares_data_collect，完成DownloadTask、幂等、重试和补缺。
3. 建立覆盖率API和前端数据中心页面。
4. 参考vn.py定义DyadTetrade自己的领域模型。
5. 参考BulletTrade建立策略接口和可复现回测，但保留自己的数据格式。
6. 先完成模拟账户和订单生命周期，再参考lite-qmt-executor开发执行。
7. 最后启用XtTrader，并加入账户白名单、双重开关、限额、审计和人工确认。

## 10. 归档 SHA256

```text
ashares_data_collect-main.zip  3dde7b00e59eca7b2d67a5b1c20db803b7d2056739d2ef466ed026907a6a3f73
bullet-trade-main.zip           94cd28a21faf2bf5dfe1d56f79f4eaf33f041565f10b26075b1add5b982cc9ee
easytrader-master.zip           cfb20c441119fab502518b7c9e34a26e4b50631108600409247518485358f235
lite-qmt-executor-main.zip      4415a3f6d1f9a12c26483a7ecd9819914e9c5cd33f8258f31110f32c831020ab
miniqmt-demo-main.zip           d36374782bd61c67f2590e032dc4ab5aaae01587b6ea3fde774915438d4fe139
OSkhQuant-main.zip              8dfc540c5bfaadb61e2d94ce10510240207eb1a03ce413fae027951f4398c003
qmt-bridge-main.zip             20718724d91364575cb0c6a24b224ff55207d6ac5ebb869f36a62d150c15c92e
QMT-MCP-main.zip                b202acabcdfc093305a09b4a1d0730299f0c863591569762f3644217eebb2e4c
quant-qmt-proxy-master.zip      b2a61e5f5322e1ed52525441b3cd92d125282836b871d085ffe72b9652abbdd8
vnpy_qmt-master.zip             710071385cdd29c4932c5c41e0dd1b007b1a686ddb1f5cbaa975d7a0ada36c33
vnpy-master.zip                 a3cfdc14caaf9e886effe951193491b33f8f9312406d33fd3b0d50a03c0ffab1
xqshare-main.zip                ba710b058f8e5cf0661f8463ad1f4fdd4c76da49b8d131003869c0284beb49eb
XtQuant-main.zip                a5566022145b1ad0788800d225853da39b458bf2ce71fd9d01df987f16feb79f
```

## 11. 最终建议

不替换当前数据中心，而是在现有结构上吸收四项成熟做法：QMT Bridge的XtData串行化、quant-qmt-proxy的Mock与权限测试、ashares_data_collect的任务补缺、BulletTrade与vn.py的策略和领域解耦。

这样可以保留DyadTetrade“小而精、Python 3.12、FastAPI、React、Parquet、DuckDB、SQLite”的路线，同时避免继承第三方项目的旧依赖、许可证和实盘安全问题。
