from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

from dyadtetrade.core.config import Settings
from dyadtetrade.modules.execution.service import QmtSimRuntime


@pytest.mark.asyncio
async def test_database_lock_is_retried_then_succeeds() -> None:
    runtime = QmtSimRuntime(Settings(_env_file=None))
    attempts = 0

    async def flaky_operation() -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise OperationalError("statement", {}, Exception("database is locked"))

    await runtime._retry_locked(flaky_operation)
    assert attempts == 3


@pytest.mark.asyncio
async def test_non_lock_database_error_fails_without_retry() -> None:
    runtime = QmtSimRuntime(Settings(_env_file=None))
    attempts = 0

    async def broken_operation() -> None:
        nonlocal attempts
        attempts += 1
        raise OperationalError("statement", {}, Exception("disk I/O error"))

    with pytest.raises(OperationalError):
        await runtime._retry_locked(broken_operation)
    assert attempts == 1
