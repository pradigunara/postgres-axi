import asyncio
import builtins
import logging
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


def test_connect_quiets_upstream_logs_before_postgres_mcp_import(monkeypatch) -> None:
    server = make_server()
    module = types.ModuleType("postgres_mcp")
    module.server = server
    real_import = builtins.__import__
    logger_names = ("postgres_mcp", "mcp", "psycopg", "psycopg.pool", "psycopg_pool")
    original_state = {
        name: (logging.getLogger(name).level, logging.getLogger(name).propagate) for name in logger_names
    }
    original_disable = logging.root.manager.disable

    for name in logger_names:
        logger = logging.getLogger(name)
        logger.setLevel(logging.NOTSET)
        logger.propagate = True

    def fake_import(name, *args, **kwargs):
        if name == "postgres_mcp":
            for logger_name in logger_names:
                logger = logging.getLogger(logger_name)
                assert logger.level == logging.CRITICAL
                assert logger.propagate is False
            assert logging.root.manager.disable == logging.ERROR
            return module
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    adapter = McpApiAdapter(database_url="postgresql://example/db", access_mode="restricted")

    async def exercise() -> None:
        async with adapter.connect():
            pass

    try:
        run(exercise())
    finally:
        for name, (level, propagate) in original_state.items():
            logger = logging.getLogger(name)
            logger.setLevel(level)
            logger.propagate = propagate
        logging.disable(original_disable)


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


def test_validate_object_metadata_returns_matching_object(monkeypatch) -> None:
    list_objects = AsyncMock(return_value=[TextContent(repr([{"schema": "app", "name": "users", "type": "table"}]))])
    server = make_server(list_objects=list_objects)
    install_fake_postgres_mcp(monkeypatch, server)
    adapter = McpApiAdapter(database_url="postgresql://example/db", access_mode="restricted")

    async def exercise():
        async with adapter.connect():
            return await adapter.validate_object_metadata("app", "users", "table")

    assert run(exercise()) == {"schema": "app", "name": "users", "type": "table"}
    list_objects.assert_awaited_once_with("app", "table")


def test_validate_object_metadata_detects_type_mismatch(monkeypatch) -> None:
    async def list_objects(schema_name, object_type):
        objects_by_type = {
            "table": [],
            "view": [{"schema": schema_name, "object_name": "users", "object_type": "view"}],
        }
        return [TextContent(repr(objects_by_type.get(object_type, [])))]

    upstream = AsyncMock(side_effect=list_objects)
    server = make_server(list_objects=upstream)
    install_fake_postgres_mcp(monkeypatch, server)
    adapter = McpApiAdapter(database_url="postgresql://example/db", access_mode="restricted")

    async def exercise() -> None:
        async with adapter.connect():
            await adapter.validate_object_metadata("app", "users", "table")

    with pytest.raises(AxiError) as exc:
        run(exercise())

    assert exc.value.code == "object_type_mismatch"
    assert exc.value.message == "app.users is a view, not a table."
    assert upstream.await_args_list[0].args == ("app", "table")
    assert ("app", "view") in [call.args for call in upstream.await_args_list]


def test_validate_object_metadata_normalizes_base_table_type_mismatch(monkeypatch) -> None:
    async def list_objects(schema_name, object_type):
        objects_by_type = {
            "view": [],
            "table": [{"schema": schema_name, "name": "users", "type": "BASE TABLE"}],
        }
        return [TextContent(repr(objects_by_type.get(object_type, [])))]

    upstream = AsyncMock(side_effect=list_objects)
    server = make_server(list_objects=upstream)
    install_fake_postgres_mcp(monkeypatch, server)
    adapter = McpApiAdapter(database_url="postgresql://example/db", access_mode="restricted")

    async def exercise() -> None:
        async with adapter.connect():
            await adapter.validate_object_metadata("app", "users", "view")

    with pytest.raises(AxiError) as exc:
        run(exercise())

    assert exc.value.code == "object_type_mismatch"
    assert exc.value.message == "app.users is a table, not a view."
    assert exc.value.hint == "Use --type table."


def test_validate_object_metadata_detects_missing_object(monkeypatch) -> None:
    list_objects = AsyncMock(return_value=[TextContent(repr([]))])
    server = make_server(list_objects=list_objects)
    install_fake_postgres_mcp(monkeypatch, server)
    adapter = McpApiAdapter(database_url="postgresql://example/db", access_mode="restricted")

    async def exercise() -> None:
        async with adapter.connect():
            await adapter.validate_object_metadata("app", "missing", "table")

    with pytest.raises(AxiError) as exc:
        run(exercise())

    assert exc.value.code == "object_not_found"
    assert exc.value.message == "No table named app.missing was found."
