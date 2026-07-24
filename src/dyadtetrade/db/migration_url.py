from __future__ import annotations

from sqlalchemy.engine import make_url

_SYNC_DRIVERS = {
    "sqlite+aiosqlite": "sqlite",
    "postgresql+asyncpg": "postgresql+psycopg",
    "postgresql+psycopg_async": "postgresql+psycopg",
}


def to_sync_database_url(database_url: str) -> str:
    """Return an Alembic-compatible synchronous URL for an application URL."""

    url = make_url(database_url)
    drivername = _SYNC_DRIVERS.get(url.drivername, url.drivername)
    return url.set(drivername=drivername).render_as_string(hide_password=False)
