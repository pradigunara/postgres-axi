import asyncio
import builtins
import sys
import types
from unittest.mock import AsyncMock

import pytest

from postgres_axi.adapter import McpApiAdapter
from postgres_axi.format import AxiError


class TextContent:
    def __init__(self, text: str) -> None:
        self.text = text


def run(coro):
    return asyncio.run(coro)


def install_fake_postgres_mcp(monkeypatch, server):
    module = types.ModuleType("postgres_mcp")
    module.server = server
    monkeypatch.setitem(sys.modules, "postgres_mcp", module)
    return module


def make_server(**methods):
    class AccessMode:
        def __init__(self, value: str) -> None:
            self.value = value

        def __eq__(self, other) -> bool:
            return isinstance(other, AccessMode) and self.value == other.value

    server = types.SimpleNamespace(
        AccessMode=AccessMode,
        current_access_mode=None,
        db_connection=types.SimpleNamespace(
            pool_connect=AsyncMock(),
            close=AsyncMock(),
        ),
    )
    for name, method in methods.items():
        setattr(server, name, method)
    return server


def test_missing_database_uri_raises_axi_error(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URI", raising=False)
    adapter = McpApiAdapter(database_url=None, access_mode="restricted")

    with pytest.raises(AxiError) as exc:
        run(adapter.connect().__aenter__())

    assert exc.value.code == "missing_database_uri"


def test_missing_postgres_mcp_import_raises_missing_dependency(monkeypatch) -> None:
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "postgres_mcp":
            raise ImportError("missing postgres_mcp")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    adapter = McpApiAdapter(database_url="postgresql://example/db", access_mode="restricted")

    with pytest.raises(AxiError) as exc:
        run(adapter.connect().__aenter__())

    assert exc.value.code == "missing_dependency"


def test_connect_sets_access_mode_connects_pool_and_closes_on_exit(monkeypatch) -> None:
    server = make_server()
    install_fake_postgres_mcp(monkeypatch, server)
    adapter = McpApiAdapter(database_url="postgresql://example/db", access_mode="unrestricted")

    async def exercise() -> None:
        async with adapter.connect() as connected:
            assert connected is adapter
            assert server.current_access_mode == server.AccessMode("unrestricted")
            server.db_connection.pool_connect.assert_awaited_once_with("postgresql://example/db")
            server.db_connection.close.assert_not_awaited()

        server.db_connection.close.assert_awaited_once_with()

    run(exercise())


@pytest.mark.parametrize(
    ("method_name", "args", "expected"),
    [
        ("list_schemas", (), [{"schema": "public"}]),
        ("list_objects", ("public", "table"), [{"name": "users"}]),
        ("get_object_details", ("public", "users", "table"), {"name": "users"}),
        ("execute_sql", ("select 1",), [{"?column?": 1}]),
        ("explain_query", ("select 1", True, [{"table": "users"}]), {"Plan": []}),
        ("analyze_workload_indexes", (64, "dta"), [{"index": "idx_users_email"}]),
        ("analyze_query_indexes", (["select * from users"], 64, "dta"), [{"index": "idx_users_id"}]),
        ("analyze_db_health", ("all",), {"status": "ok"}),
        ("get_top_queries", ("total_time", 5), [{"query": "select 1"}]),
    ],
)
def test_wrapper_methods_call_upstream_and_unwrap_text(monkeypatch, method_name, args, expected) -> None:
    upstream = AsyncMock(return_value=[TextContent(repr(expected))])
    server = make_server(**{method_name: upstream})
    install_fake_postgres_mcp(monkeypatch, server)
    adapter = McpApiAdapter(database_url="postgresql://example/db", access_mode="restricted")

    async def exercise():
        async with adapter.connect():
            return await getattr(adapter, method_name)(*args)

    assert run(exercise()) == expected
    upstream.assert_awaited_once_with(*args)


def test_call_before_connect_raises_not_connected() -> None:
    adapter = McpApiAdapter(database_url="postgresql://example/db", access_mode="restricted")

    with pytest.raises(AxiError) as exc:
        run(adapter._call("list_schemas"))

    assert exc.value.code == "not_connected"


def test_upstream_exceptions_become_upstream_call_failed(monkeypatch) -> None:
    upstream = AsyncMock(side_effect=RuntimeError("boom"))
    server = make_server(list_schemas=upstream)
    install_fake_postgres_mcp(monkeypatch, server)
    adapter = McpApiAdapter(database_url="postgresql://example/db", access_mode="restricted")

    async def exercise() -> None:
        async with adapter.connect():
            await adapter.list_schemas()

    with pytest.raises(AxiError) as exc:
        run(exercise())

    assert exc.value.code == "upstream_call_failed"
    assert exc.value.message == "boom"
