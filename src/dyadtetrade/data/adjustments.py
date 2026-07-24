from __future__ import annotations

from collections.abc import Iterable
from typing import NamedTuple

import numpy as np
import pandas as pd

from dyadtetrade.data.models import AdjustmentMode

PRICE_COLUMNS = ("open", "high", "low", "close", "pre_close", "settlement_price")


class _Action(NamedTuple):
    action_date: int
    interest: float
    stock_bonus: float
    stock_gift: float
    allot_num: float
    allot_price: float


def adjust_prices(
    frame: pd.DataFrame,
    actions: pd.DataFrame,
    *,
    mode: AdjustmentMode,
) -> pd.DataFrame:
    result = frame.copy(deep=True)
    if mode == "none" or result.empty or actions.empty:
        return result
    if "trade_date" not in result:
        raise ValueError("bar frame requires trade_date for point-in-time adjustment")
    normalized_actions = _normalize_actions(actions)
    if normalized_actions.empty:
        return result
    if mode in {"front_ratio", "back_ratio"}:
        multipliers = _ratio_multipliers(result["trade_date"], normalized_actions, mode)
        for column in _present_price_columns(result):
            result[column] = pd.to_numeric(result[column], errors="coerce") * multipliers
        return result
    action_rows = _action_rows(normalized_actions)
    for index, raw_date in result["trade_date"].items():
        trade_date = int(raw_date)
        relevant = (
            [action for action in action_rows if action.action_date > trade_date]
            if mode == "front"
            else [action for action in action_rows if action.action_date <= trade_date]
        )
        for column in _present_price_columns(result):
            value = result.at[index, column]
            if pd.isna(value):
                continue
            adjusted = float(str(value))
            if mode == "front":
                for action in relevant:
                    adjusted = _front_event(adjusted, action)
            else:
                for action in reversed(relevant):
                    adjusted = _back_event(adjusted, action)
            result.at[index, column] = adjusted
    return result


def _normalize_actions(actions: pd.DataFrame) -> pd.DataFrame:
    result = actions.copy(deep=True)
    defaults = {
        "interest": 0.0,
        "stock_bonus": 0.0,
        "stock_gift": 0.0,
        "allot_num": 0.0,
        "allot_price": 0.0,
        "gugai": 0.0,
        "dr": 1.0,
    }
    if "action_date" not in result:
        if result.index.name == "action_date":
            result = result.reset_index()
        else:
            raise ValueError("corporate actions require action_date")
    result["action_date"] = pd.to_numeric(result["action_date"], errors="raise").astype(int)
    for column, default in defaults.items():
        if column not in result:
            result[column] = default
        result[column] = pd.to_numeric(result[column], errors="coerce").fillna(default)
    return result.sort_values("action_date", kind="stable").reset_index(drop=True)


def _ratio_multipliers(dates: pd.Series, actions: pd.DataFrame, mode: AdjustmentMode) -> pd.Series:
    action_dates = actions["action_date"].to_numpy(dtype=int)
    dr_values = actions["dr"].to_numpy(dtype=float)
    if np.any(~np.isfinite(dr_values)) or np.any(dr_values <= 0):
        raise ValueError("corporate action dr must be finite and positive")
    cumulative = np.cumprod(dr_values)
    positions = np.searchsorted(action_dates, dates.to_numpy(dtype=int), side="right") - 1
    raw = np.where(positions >= 0, cumulative[np.maximum(positions, 0)], 1.0)
    if mode == "front_ratio":
        anchor = float(cumulative[-1])
        raw = raw / anchor if anchor != 0 else raw
    return pd.Series(raw, index=dates.index, dtype=float)


def _present_price_columns(frame: pd.DataFrame) -> Iterable[str]:
    return (column for column in PRICE_COLUMNS if column in frame)


def _action_rows(actions: pd.DataFrame) -> list[_Action]:
    return [
        _Action(
            action_date=int(row["action_date"]),
            interest=float(row["interest"]),
            stock_bonus=float(row["stock_bonus"]),
            stock_gift=float(row["stock_gift"]),
            allot_num=float(row["allot_num"]),
            allot_price=float(row["allot_price"]),
        )
        for row in actions.to_dict(orient="records")
    ]


def _event_coefficients(action: _Action) -> tuple[float, float]:
    denominator = 1.0 + action.stock_bonus + action.stock_gift + action.allot_num
    if denominator <= 0:
        raise ValueError("corporate action share denominator must be positive")
    a = 1.0 / denominator
    b = (action.allot_num * action.allot_price - action.interest) / denominator
    return a, b


def _front_event(value: float, action: _Action) -> float:
    a, b = _event_coefficients(action)
    return a * value + b


def _back_event(value: float, action: _Action) -> float:
    a, b = _event_coefficients(action)
    return (value - b) / a
