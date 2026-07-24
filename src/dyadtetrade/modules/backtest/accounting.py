from __future__ import annotations

from decimal import Decimal

from dyadtetrade.domain import Account


def assert_accounting_identity(account: Account) -> Decimal:
    account.assert_identity()
    expected = account.cash + account.positions_value + account.accrued
    if account.total_assets != expected:
        raise ArithmeticError("total assets accounting identity violated")
    return expected
