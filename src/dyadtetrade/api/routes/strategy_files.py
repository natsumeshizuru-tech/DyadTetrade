from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from dyadtetrade.db.session import get_session
from dyadtetrade.modules.strategy.files import strategy_file_service

router = APIRouter(prefix="/strategy-files", tags=["strategy-files"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class StrategyFileCreate(BaseModel):
    name: str
    code: str | None = None
    clone_from: str | None = None


class StrategyFileUpdate(BaseModel):
    code: str = Field(min_length=1, max_length=200_000)


class SmokeRequest(BaseModel):
    symbol: str = "600519.SH"
    parameters: dict[str, Any] = Field(default_factory=dict)


@router.get("")
async def list_strategy_files(session: SessionDep) -> list[dict[str, object]]:
    return await strategy_file_service.list(session)


@router.post("")
async def create_strategy_file(
    request: StrategyFileCreate, session: SessionDep
) -> dict[str, object]:
    try:
        return await strategy_file_service.create(
            session, request.name, request.code, request.clone_from
        )
    except (ValueError, FileExistsError, FileNotFoundError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{name}")
async def get_strategy_file(name: str, session: SessionDep) -> dict[str, object]:
    try:
        return await strategy_file_service.get(session, name)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{name}")
async def update_strategy_file(
    name: str, request: StrategyFileUpdate, session: SessionDep
) -> dict[str, object]:
    try:
        return await strategy_file_service.update(session, name, request.code)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{name}/validate")
async def validate_strategy_file(name: str, session: SessionDep) -> dict[str, object]:
    try:
        return await strategy_file_service.validate(session, name)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{name}/smoke-backtest")
async def smoke_strategy_file(
    name: str, request: SmokeRequest, session: SessionDep
) -> dict[str, object]:
    try:
        return await strategy_file_service.smoke(
            session, name, symbol=request.symbol, parameters=request.parameters
        )
    except (PermissionError, RuntimeError, TimeoutError, FileNotFoundError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{name}/publish")
async def publish_strategy_file(name: str, session: SessionDep) -> dict[str, object]:
    try:
        return await strategy_file_service.publish(session, name)
    except (PermissionError, FileNotFoundError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{name}/start")
async def start_strategy_file(name: str, session: SessionDep) -> dict[str, object]:
    try:
        return await strategy_file_service.start(session, name)
    except (PermissionError, FileNotFoundError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{name}/stop")
async def stop_strategy_file(name: str, session: SessionDep) -> dict[str, object]:
    try:
        return await strategy_file_service.stop(session, name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
