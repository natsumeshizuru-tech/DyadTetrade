from __future__ import annotations

import re

SYMBOL_PATTERN = re.compile(r"^(?P<code>\d{6})(?:[.](?P<market>SH|SZ|BJ))?$")


def normalize_symbol(value: str) -> str:
    text = value.strip().upper().replace("_", ".")
    match = SYMBOL_PATTERN.fullmatch(text)
    if not match:
        raise ValueError(f"无效证券代码：{value}")

    code = match.group("code")
    market = match.group("market") or infer_market(code)
    return f"{code}.{market}"


def infer_market(code: str) -> str:
    if code.startswith(("4", "8", "9")):
        return "BJ"
    if code.startswith(("5", "6", "7")):
        return "SH"
    return "SZ"


def is_a_share_symbol(value: str, *, include_bj: bool = True) -> bool:
    try:
        symbol = normalize_symbol(value)
    except ValueError:
        return False

    code, market = symbol.split(".")
    if market == "SH":
        return code.startswith(("600", "601", "603", "605", "688", "689"))
    if market == "SZ":
        return code.startswith(("000", "001", "002", "003", "300", "301"))
    if market == "BJ" and include_bj:
        return code.startswith(("4", "8", "9"))
    return False


def infer_asset_type(value: str) -> str:
    symbol = normalize_symbol(value)
    code, market = symbol.split(".")
    if is_a_share_symbol(symbol):
        return "stock"
    if market == "SH" and code.startswith(("000", "880")):
        return "index"
    if market == "SZ" and code.startswith(("399", "980")):
        return "index"
    if code.startswith(("15", "16", "18", "50", "51", "52", "56", "58")):
        return "etf"
    return "other"
