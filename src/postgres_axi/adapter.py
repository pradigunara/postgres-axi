from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from .format import AxiError, unwrap_mcp_text

OBJECT_TYPES = ("table", "view", "sequence", "extension")


class McpApiAdapter:
    def __init__(self, database_url: str | None, access_mode: str) -> None:
        self.database_url = database_url or os.environ.get("DATABASE_URI")
        self.access_mode = access_mode
        self._server: Any | None = None

    @asynccontextmanager
    async def connect(self) -> AsyncIterator["McpApiAdapter"]:
        if not self.database_url:
            raise AxiError(
                code="missing_database_uri",
                message="No database URL provided.",
                hint="Set DATABASE_URI or pass --database-url postgresql://...",
            )

        _quiet_upstream_logs()
        try:
            from postgres_mcp import server
        except ImportError as exc:
            raise AxiError(
                code="missing_dependency",
                message="Could not import postgres_mcp.",
                hint="Install postgres-mcp in this environment.",
            ) from exc

        self._server = server
        try:
            server.current_access_mode = server.AccessMode(self.access_mode)
            await server.db_connection.pool_connect(self.database_url)
            yield self
        except AxiError:
            raise
        except Exception as exc:
            raise AxiError(code="connection_failed", message=str(exc)) from exc
        finally:
            try:
                await server.db_connection.close()
            except Exception:
                pass

    async def list_schemas(self) -> Any:
        return unwrap_mcp_text(await self._call("list_schemas"))

    async def list_objects(self, schema_name: str, object_type: str) -> Any:
        return unwrap_mcp_text(await self._call("list_objects", schema_name, object_type))

    async def get_object_details(self, schema_name: str, object_name: str, object_type: str) -> Any:
        return unwrap_mcp_text(await self._call("get_object_details", schema_name, object_name, object_type))

    async def validate_object_metadata(self, schema_name: str, object_name: str, object_type: str) -> dict[str, Any]:
        expected = await self._find_object_metadata(schema_name, object_name, object_type)
        if expected is not None:
            return expected

        for candidate_type in OBJECT_TYPES:
            if candidate_type == object_type:
                continue
            candidate = await self._find_object_metadata(schema_name, object_name, candidate_type)
            if candidate is not None:
                actual_type = _object_type(candidate, candidate_type)
                raise AxiError(
                    code="object_type_mismatch",
                    message=f"{schema_name}.{object_name} is a {actual_type}, not a {object_type}.",
                    hint=f"Use --type {actual_type}.",
                )

        raise AxiError(
            code="object_not_found",
            message=f"No {object_type} named {schema_name}.{object_name} was found.",
        )

    async def execute_sql(self, sql: str) -> Any:
        return unwrap_mcp_text(await self._call("execute_sql", sql))

    async def explain_query(self, sql: str, analyze: bool, hypothetical_indexes: list[dict[str, Any]]) -> Any:
        return unwrap_mcp_text(await self._call("explain_query", sql, analyze, hypothetical_indexes))

    async def analyze_workload_indexes(self, max_index_size_mb: int, method: str) -> Any:
        return unwrap_mcp_text(await self._call("analyze_workload_indexes", max_index_size_mb, method))

    async def analyze_query_indexes(self, queries: list[str], max_index_size_mb: int, method: str) -> Any:
        return unwrap_mcp_text(await self._call("analyze_query_indexes", queries, max_index_size_mb, method))

    async def analyze_db_health(self, health_type: str) -> Any:
        return unwrap_mcp_text(await self._call("analyze_db_health", health_type))

    async def get_top_queries(self, sort_by: str, limit: int) -> Any:
        return unwrap_mcp_text(await self._call("get_top_queries", sort_by, limit))

    async def _find_object_metadata(self, schema_name: str, object_name: str, object_type: str) -> dict[str, Any] | None:
        objects = await self.list_objects(schema_name, object_type)
        if not isinstance(objects, list):
            return None

        for obj in objects:
            if not isinstance(obj, dict):
                continue
            if _object_name(obj) == object_name:
                return obj
        return None

    async def _call(self, name: str, *args: Any) -> Any:
        if self._server is None:
            raise AxiError(code="not_connected", message="Adapter is not connected.")
        try:
            return await getattr(self._server, name)(*args)
        except AxiError:
            raise
        except Exception as exc:
            raise AxiError(code="upstream_call_failed", message=str(exc)) from exc


def _quiet_upstream_logs() -> None:
    logging.disable(logging.ERROR)
    for name in ("postgres_mcp", "mcp", "psycopg", "psycopg.pool", "psycopg_pool"):
        logger = logging.getLogger(name)
        logger.setLevel(logging.CRITICAL)
        logger.propagate = False


def _object_name(obj: dict[str, Any]) -> str | None:
    value = obj.get("name", obj.get("object_name"))
    return str(value) if value is not None else None


def _object_type(obj: dict[str, Any], default: str) -> str:
    value = obj.get("type", obj.get("object_type", default))
    normalized = str(value).lower().replace(" ", "_")
    if normalized == "base_table":
        return "table"
    return normalized
