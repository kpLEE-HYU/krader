"""Async SQLite database wrapper."""

import logging
from pathlib import Path

import aiosqlite

from krader.persistence.models import SCHEMA

logger = logging.getLogger(__name__)


class Database:
    """Async SQLite database connection manager."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._connection: aiosqlite.Connection | None = None

    @property
    def connection(self) -> aiosqlite.Connection:
        """Get the active database connection."""
        if self._connection is None:
            raise RuntimeError("Database not connected")
        return self._connection

    async def connect(self) -> None:
        """Open the database connection and initialize schema."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self._path)
        self._connection.row_factory = aiosqlite.Row
        await self._connection.execute("PRAGMA journal_mode=WAL")
        await self._connection.execute("PRAGMA foreign_keys=ON")
        await self._init_schema()
        logger.info("Database connected: %s", self._path)

    async def disconnect(self) -> None:
        """Close the database connection."""
        if self._connection:
            await self._connection.close()
            self._connection = None
            logger.info("Database disconnected")

    async def _init_schema(self) -> None:
        """Initialize database schema."""
        for statement in SCHEMA:
            await self._connection.execute(statement)
        await self._connection.commit()
        logger.debug("Database schema initialized")

    async def execute(
        self, sql: str, parameters: tuple | dict | None = None
    ) -> aiosqlite.Cursor:
        """Execute a SQL statement."""
        if parameters is None:
            return await self.connection.execute(sql)
        return await self.connection.execute(sql, parameters)

    async def executemany(
        self, sql: str, parameters: list[tuple | dict]
    ) -> aiosqlite.Cursor:
        """Execute a SQL statement with multiple parameter sets."""
        return await self.connection.executemany(sql, parameters)

    async def fetchone(
        self, sql: str, parameters: tuple | dict | None = None
    ) -> aiosqlite.Row | None:
        """Execute a query and fetch one row."""
        cursor = await self.execute(sql, parameters)
        return await cursor.fetchone()

    async def fetchall(
        self, sql: str, parameters: tuple | dict | None = None
    ) -> list[aiosqlite.Row]:
        """Execute a query and fetch all rows."""
        cursor = await self.execute(sql, parameters)
        return await cursor.fetchall()

    async def commit(self) -> None:
        """Commit the current transaction."""
        await self.connection.commit()
