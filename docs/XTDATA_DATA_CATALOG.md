# XtData data catalog reference

DyadResearch 与 DyadTetrade 的下载目录以迅投官方知识库为准，并由当前 MiniQMT 的 XtData 版本与账号权限在运行时再次校验。

官方依据：

- [XtQuant.XtData 行情模块](https://dict.thinktrader.net/nativeApi/xtdata.html)：历史行情批量下载、财务、板块、历史合约、节假日、可转债和 ETF 接口，以及标准和投研特色周期。
- [XtQuant 版本记录](https://dict.thinktrader.net/nativeApi/download_xtquant.html)：公告、涨跌停连板、港股通、ETF IOPV、历史主力合约等后续周期。
- [行业概念数据](https://dict.thinktrader.net/dictionary/industry.html)：板块和历史合约下载前置条件。
- [债券数据](https://dict.thinktrader.net/dictionary/bond.html)：可转债基础信息下载与权限说明。

产品目录覆盖：

- Level1：`tick`、`1m`、`5m`、`15m`、`30m`、`1h`、`1d`、`1w`、`1mon`、`1q`、`1hy`、`1y`。
- 投研特色：期货仓单/席位、互动问答、逐笔成交统计、退市/待发可转债、ST 历史、港股通资金流、分红方案、历史合约/期权/主力、涨跌停、快照指标、公告、连板、港股通持仓与 ETF IOPV。
- 独立下载族：财务报表、板块分类、历史合约、节假日、可转债、ETF 申赎清单、指数权重。

Level2 快照、逐笔委托、逐笔成交和委托队列仅作为当日实时订阅/读取能力。官方文档说明这类数据不保存跨日历史，因此本产品不会把它们标成历史下载类别。

原始 `1d` 和 `1m` 可写入两套软件共用的 SQLite 数据层；其他周期保留在 MiniQMT 缓存，由账号权限决定是否能取得。Research 的查询连接继续强制只读，写入只经 Tetrade 数据控制面执行。
