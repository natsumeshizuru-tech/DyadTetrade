# 全市场自动执行安全手册

## 目标与边界

DyadTetrade 不把“全市场”实现成 `*` 通配符。系统每天根据证券目录、最近完整交易日数据和交易规格生成不可变快照，经过复核、确认和哈希校验后，Trading Engine 才能加载。策略还需要独立的当日发布记录，订单仍逐笔经过 OMS、行情新鲜度检查和组合风控。

`qmt_live` 默认关闭。本文建立的是可审计、可恢复、失败即关闭的能力；没有模拟柜台完整验收、真实账户白名单和人工日内确认时，不得进入真实资金环境。

## 每日数据与执行池

收盘后先增量更新日线：

```powershell
python scripts/postclose_daily_update.py --lookback-days 10
```

生成、复核并激活模拟柜台执行池：

```powershell
python scripts/build_execution_universe.py --mode qmt_sim --review --activate `
  --actor local-operator --confirmation I_CONFIRM_QMT_SIM_UNIVERSE
```

设置 `DYADTETRADE_ENABLE_DYNAMIC_EXECUTION_UNIVERSE=true` 后，模拟工作台启动时若找不到当日 active 快照，会自动执行同一套 build → review → activate 流程；若最近完整交易日数据不合格则启动失败。`qmt_live` 不会走这条自动激活路径。

默认过滤规则：仅沪深京股票和 ETF；剔除 ST、退市或退市整理、上市不足 20 天、代码族不符合 A 股/ETF 规则、交易规格异常、无日线或日线未覆盖最近完整交易日的标的。快照记录成员数、拒绝原因、数据日、过滤参数和 SHA-256 指纹，并于下一个上海自然日零时失效。

辅助脚本拒绝激活 `qmt_live`。真实候选池只能通过 API 分开执行 build、review、activate，并输入 `I_CONFIRM_FULL_MARKET_LIVE_UNIVERSE`，防止脚本静默放行。

## 双重发布门禁

自动订单必须同时满足：

1. Trading Engine 加载的当日执行池处于 `active`，模式、日期、成员数和哈希一致。
2. 策略存在当日 `active` 发布记录，策略 ID、名称、版本、参数哈希和执行池发布 ID 完全一致。
3. `DYADTETRADE_ENABLE_AUTOMATED_STRATEGY_EXECUTION=true`。
4. `qmt_live` 还需当天输入独立确认短语 `I_CONFIRM_AUTOMATED_LIVE_EXECUTION`。

项目内 `tick_momentum_small_cap` 明确保持 Paper-only，服务端拒绝为它创建 `qmt_live` 发布记录。

主要 API：

```text
POST /api/v1/execution/universe/releases
POST /api/v1/execution/universe/releases/{release_id}/review
POST /api/v1/execution/universe/releases/{release_id}/activate
POST /api/v1/execution/universe/releases/{release_id}/revoke
POST /api/v1/execution/strategy-releases
POST /api/v1/execution/strategy-releases/{release_id}/activate
POST /api/v1/execution/strategy-releases/{release_id}/revoke
POST /api/v1/execution/automated-orders
```

`automated-orders` 使用 `strategy_release_id + signal_id` 生成稳定幂等键。QMT 返回的异步 seq 只代表受理序号，不创建成交；只有券商成交回调或主动成交查询可以写入 QMT 成交账本。

## 组合风控

除原有单笔、单日、单标的、行情陈旧、断线、账户异常、重复订单和每分钟频率限制外，当前增加：

- 全组合持仓市值上限；
- 总资产仓位比例上限；
- 每日订单次数上限；
- 单策略每日成交名义金额上限；
- 重启后从持久化订单恢复日累计值；
- NORMAL、REDUCE_ONLY、CANCEL_ONLY、HALTED、RECOVERY 风险状态机；
- 全局、账户和策略停止开关。

任一门禁失败都拒绝订单，不会降级为无约束下单。

## 模拟运行

将每日模拟执行池 ID 注入当前 PowerShell 会话：

```powershell
$env:DYADTETRADE_ENABLE_DYNAMIC_EXECUTION_UNIVERSE = "true"
$env:DYADTETRADE_EXECUTION_UNIVERSE_RELEASE_ID = "" # qmt_sim 可自动解析或创建当日快照
$env:DYADTETRADE_ENABLE_AUTOMATED_STRATEGY_EXECUTION = "false"
.\scripts\start_trading_workbench.ps1 -ExecutionMode qmt_sim
```

先保持自动策略关闭，只做连接、资产/持仓/订单/成交查询和重启对账。确认一手限价委托、回调、撤单、幂等和 Kill Switch 后，再在 `qmt_sim` 创建策略发布记录并打开自动策略开关。

## 真实资金前仍需人工完成

- 券商侧开通和核对真实账户权限；
- 使用独立真实账户白名单，核对 `userdata_mini`；
- 连续多个交易日完成 qmt_sim 全天运行、断线恢复、回调乱序和日终对账；
- 每天复核数据覆盖、执行池拒绝分布、策略版本和参数哈希；
- 以一个白名单标的、一手、极低单笔和单日限额灰度；
- 验证外部告警、人工 Kill Switch 和应急撤单通道。

在这些条件满足前，必须保持 `DYADTETRADE_ENABLE_LIVE_TRADING=false`。
