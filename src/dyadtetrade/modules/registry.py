from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ModuleInfo(BaseModel):
    key: str
    name: str
    phase: Literal["foundation", "trading", "operations"]
    status: Literal["scaffold", "available"] = "scaffold"


MODULES = [
    ModuleInfo(key="realtime", name="实时行情与订阅", phase="foundation", status="available"),
    ModuleInfo(key="paper", name="Paper 模拟交易", phase="trading", status="available"),
    ModuleInfo(key="broker", name="QMT 交易网关", phase="trading", status="available"),
    ModuleInfo(key="oms", name="订单管理系统", phase="trading", status="available"),
    ModuleInfo(key="execution", name="策略执行与发布门禁", phase="trading", status="available"),
    ModuleInfo(key="risk", name="风险控制系统", phase="trading", status="available"),
    ModuleInfo(key="monitoring", name="账户与策略监控", phase="operations", status="available"),
    ModuleInfo(key="reconciliation", name="券商对账", phase="operations", status="available"),
    ModuleInfo(key="audit", name="交易审计与交割单", phase="operations", status="available"),
    ModuleInfo(key="frontend", name="前端操作界面", phase="operations", status="available"),
]
