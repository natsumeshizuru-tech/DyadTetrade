# Project Skill Source Map

项目级 Skill 的优先来源如下。Skill 只保留操作边界和检查表，长 API 表继续留在知识库原文中。

| Skill | 主要本地来源 |
|---|---|
| miniqmt-environment-diagnostics | XtQuant 技术参考总览、常见问题、现有 `integrations/xtquant.py` |
| xtdata-market-data | XtData 校正版、完整实例、市场数据中心文档与测试 |
| xttrader-trading-gateway | XtTrader 校正版、完整实例；只提取生命周期与回调语义 |
| qmt-order-execution-safety | 普通算法、智能算法、网格策略和 QMT 操作资料；只形成安全检查表 |
| qmt-bar-signal-semantics | QMT Python API PDF 的模型事件语义与项目防前视规则 |
| backtrader-xtquant-integration | Backtrader 包、XtData 校正版、项目 Parquet 合同 |
| a-share-backtest-realism | 因子/量化资料与项目 A 股数据合同；费率保持可配置 |
| quant-data-quality | 项目 `raw → clean → trusted → bt_ready` 实现与质量测试 |
| factor-research-a-share | 因子投资方法、101 Alphas、国泰君安价量因子资料 |
| backtest-validation-and-overfitting | Walk-forward、时序验证和项目稳健性要求 |
| quant-risk-performance-reporting | 项目回测报告口径与组合分析要求 |
| live-trading-release-gate | 项目实盘关闭配置和交易安全需求；不含真实账号 |
| vectorbt-* | marketcalls 上游工作流概念，经本项目重新实现并移除印度/OpenAlgo/密钥假设 |

未找到用户点名的两个 TXT 文件：`miniQMT文档与代码示例.txt`、`backtrader技术文档.txt`。
若以后补入，应先做静态审查，再更新对应 Skill 的 `references/` 摘要，不能直接复制敏感配置。
