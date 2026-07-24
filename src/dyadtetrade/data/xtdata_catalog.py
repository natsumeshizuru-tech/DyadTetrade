from __future__ import annotations

from typing import Any

HISTORY_PERIODS: tuple[dict[str, Any], ...] = (
    {"value": "tick", "label": "分笔 Tick", "group": "Level1", "entitlement": "standard"},
    {"value": "1m", "label": "1 分钟", "group": "Level1", "entitlement": "standard"},
    {"value": "5m", "label": "5 分钟", "group": "Level1", "entitlement": "standard"},
    {"value": "15m", "label": "15 分钟", "group": "Level1", "entitlement": "standard"},
    {"value": "30m", "label": "30 分钟", "group": "Level1", "entitlement": "standard"},
    {"value": "1h", "label": "1 小时", "group": "Level1", "entitlement": "standard"},
    {"value": "1d", "label": "日线", "group": "Level1", "entitlement": "standard"},
    {"value": "1w", "label": "周线", "group": "Level1", "entitlement": "standard"},
    {"value": "1mon", "label": "月线", "group": "Level1", "entitlement": "standard"},
    {"value": "1q", "label": "季线", "group": "Level1", "entitlement": "standard"},
    {"value": "1hy", "label": "半年线", "group": "Level1", "entitlement": "standard"},
    {"value": "1y", "label": "年线", "group": "Level1", "entitlement": "standard"},
    {
        "value": "warehousereceipt",
        "label": "期货仓单",
        "group": "投研特色",
        "entitlement": "research",
    },
    {
        "value": "futureholderrank",
        "label": "期货席位",
        "group": "投研特色",
        "entitlement": "research",
    },
    {
        "value": "interactiveqa",
        "label": "互动问答",
        "group": "投研特色",
        "entitlement": "research",
    },
    {
        "value": "transactioncount1m",
        "label": "逐笔成交统计（分钟）",
        "group": "投研特色",
        "entitlement": "vip",
    },
    {
        "value": "transactioncount1d",
        "label": "逐笔成交统计（日）",
        "group": "投研特色",
        "entitlement": "vip",
    },
    {
        "value": "delistchangebond",
        "label": "退市可转债",
        "group": "投研特色",
        "entitlement": "research",
    },
    {
        "value": "replacechangebond",
        "label": "待发可转债",
        "group": "投研特色",
        "entitlement": "research",
    },
    {
        "value": "specialtreatment",
        "label": "ST 变更历史",
        "group": "投研特色",
        "entitlement": "research",
    },
    {
        "value": "northfinancechange1m",
        "label": "港股通资金流（分钟）",
        "group": "投研特色",
        "entitlement": "vip",
    },
    {
        "value": "northfinancechange1d",
        "label": "港股通资金流（日）",
        "group": "投研特色",
        "entitlement": "vip",
    },
    {
        "value": "dividendplaninfo",
        "label": "红利分配方案",
        "group": "投研特色",
        "entitlement": "research",
    },
    {
        "value": "historycontract",
        "label": "过期合约列表",
        "group": "合约",
        "entitlement": "research",
    },
    {
        "value": "optionhistorycontract",
        "label": "历史期权合约",
        "group": "合约",
        "entitlement": "research",
    },
    {
        "value": "historymaincontract",
        "label": "历史主力合约",
        "group": "合约",
        "entitlement": "research",
    },
    {
        "value": "stoppricedata",
        "label": "涨跌停数据",
        "group": "投研特色",
        "entitlement": "research",
    },
    {
        "value": "snapshotindex",
        "label": "快照指标",
        "group": "投研特色",
        "entitlement": "research",
    },
    {
        "value": "announcement",
        "label": "交易所公告",
        "group": "投研特色",
        "entitlement": "vip",
    },
    {
        "value": "limitupperformance",
        "label": "涨跌停连板表现",
        "group": "投研特色",
        "entitlement": "vip",
    },
    {
        "value": "hktdetails",
        "label": "港股通持股明细",
        "group": "投研特色",
        "entitlement": "vip",
    },
    {
        "value": "hktstatistics",
        "label": "港股通统计",
        "group": "投研特色",
        "entitlement": "vip",
    },
    {
        "value": "etfiopv1m",
        "label": "ETF IOPV（分钟）",
        "group": "ETF",
        "entitlement": "research",
    },
    {
        "value": "etfiopv1d",
        "label": "ETF IOPV（日）",
        "group": "ETF",
        "entitlement": "research",
    },
)


DOWNLOAD_DATASETS: tuple[dict[str, Any], ...] = (
    {
        "value": "history",
        "label": "历史行情 / 特色周期",
        "download_api": "download_history_data2",
        "requires_symbols": True,
        "requires_period": True,
        "supports_shared_import": True,
    },
    {
        "value": "financial",
        "label": "财务报表",
        "download_api": "download_financial_data2",
        "requires_symbols": True,
        "requires_period": False,
        "supports_shared_import": True,
    },
    {
        "value": "sector",
        "label": "行业 / 概念 / 板块",
        "download_api": "download_sector_data",
        "requires_symbols": False,
        "requires_period": False,
        "supports_shared_import": True,
    },
    {
        "value": "history_contracts",
        "label": "退市及历史合约",
        "download_api": "download_history_contracts",
        "requires_symbols": False,
        "requires_period": False,
        "supports_shared_import": False,
    },
    {
        "value": "holiday",
        "label": "节假日 / 交易日历依赖",
        "download_api": "download_holiday_data",
        "requires_symbols": False,
        "requires_period": False,
        "supports_shared_import": False,
    },
    {
        "value": "convertible_bond",
        "label": "可转债基础信息",
        "download_api": "download_cb_data",
        "requires_symbols": False,
        "requires_period": False,
        "supports_shared_import": False,
        "entitlement": "vip",
    },
    {
        "value": "etf",
        "label": "ETF 申赎清单",
        "download_api": "download_etf_info",
        "requires_symbols": False,
        "requires_period": False,
        "supports_shared_import": False,
    },
    {
        "value": "index_weight",
        "label": "指数权重",
        "download_api": "download_index_weight",
        "requires_symbols": False,
        "requires_period": False,
        "supports_shared_import": True,
    },
)


def catalog_payload(capabilities: dict[str, Any]) -> dict[str, Any]:
    available = {str(item) for item in capabilities.get("available_periods", [])}
    periods = [
        {**item, "reported_by_client": item["value"] in available} for item in HISTORY_PERIODS
    ]
    functions = capabilities.get("functions", {})
    datasets = [
        {
            **item,
            "available": bool(functions.get(item["download_api"], False)),
        }
        for item in DOWNLOAD_DATASETS
    ]
    return {
        "source": "xtdata",
        "capabilities": capabilities,
        "datasets": datasets,
        "periods": periods,
        "shared_import_periods": ["1d", "1m"],
        "level2_history_downloadable": False,
        "notes": [
            "Level2 接口仅提供当日实时数据，跨交易日清理，不列为历史下载类别。",
            "投研版与 VIP 类别是否可用以当前 MiniQMT 账号权限为准。",
            "1d/1m 可导入两套软件共用的 SQLite；其他周期保留在 MiniQMT 缓存。",
        ],
    }
