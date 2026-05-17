from __future__ import annotations

import argparse
import asyncio
from typing import Any

from postgres_axi.cli import build_parser, dispatch, validate_args
from postgres_axi.format import AxiError


class FakeAdapter:
    def __init__(self, *, fail_query_indexes: bool = False) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fail_query_indexes = fail_query_indexes

    async def list_schemas(self) -> Any:
        self.calls.append(("list_schemas", ()))
        return [{"schema": "public"}, {"schema": "app"}]

    async def list_objects(self, schema_name: str, object_type: str) -> Any:
        self.calls.append(("list_objects", (schema_name, object_type)))
        if object_type == "extension":
            return [{"schema": schema_name, "name": "pg_stat_statements", "type": object_type}]
        return [
            {"schema": schema_name, "name": "users", "type": object_type},
            {"schema": schema_name, "name": "app_users", "type": object_type},
            {"schema": schema_name, "name": "audit_log", "type": object_type},
        ]

    async def get_object_details(self, schema_name: str, object_name: str, object_type: str) -> Any:
        self.calls.append(("get_object_details", (schema_name, object_name, object_type)))
        return {
            "schema": schema_name,
            "name": object_name,
            "type": object_type,
            "columns": [{"name": "id", "type": "integer"}],
        }

    async def validate_object_metadata(self, schema_name: str, object_name: str, object_type: str) -> dict[str, Any]:
        self.calls.append(("validate_object_metadata", (schema_name, object_name, object_type)))
        if object_name == "missing":
            raise AxiError(code="object_not_found", message=f"No {object_type} named {schema_name}.{object_name} was found.")
        if object_name == "actual_view" and object_type == "table":
            raise AxiError(
                code="object_type_mismatch",
                message=f"{schema_name}.{object_name} is a view, not a table.",
                hint="Use --type view.",
            )
        return {"schema": schema_name, "name": object_name, "type": object_type}

    async def execute_sql(self, sql: str) -> Any:
        self.calls.append(("execute_sql", (sql,)))
        return [{"id": 1, "email": "a@example.test"}]

    async def explain_query(self, sql: str, analyze: bool, hypothetical_indexes: list[dict[str, Any]]) -> Any:
        self.calls.append(("explain_query", (sql, analyze, hypothetical_indexes)))
        if hypothetical_indexes:
            return "Index Scan using idx_users_email\nHypothetical Indexes: idx_users_email"
        return "Seq Scan on users\nFilter: email = 'a@example.test'"

    async def get_top_queries(self, sort_by: str, limit: int) -> Any:
        self.calls.append(("get_top_queries", (sort_by, limit)))
        if sort_by == "resources":
            raise AxiError(code="upstream_error", message="resource-intensive queries: division by zero")
        return [{"query": "select 1", "calls": 7, "mean_time": 1.25}]

    async def analyze_db_health(self, health_type: str) -> Any:
        self.calls.append(("analyze_db_health", (health_type,)))
        return {"status": "ok", "checks": [{"name": "connections", "status": "ok"}]}

    async def analyze_workload_indexes(self, max_index_size_mb: int, method: str) -> Any:
        self.calls.append(("analyze_workload_indexes", (max_index_size_mb, method)))
        return [{"table": "users", "columns": ["email"], "method": method}]

    async def analyze_query_indexes(self, queries: list[str], max_index_size_mb: int, method: str) -> Any:
        self.calls.append(("analyze_query_indexes", (queries, max_index_size_mb, method)))
        if self.fail_query_indexes:
            raise AxiError(code="index_analysis_failed", message="HypoPG is unavailable.")
        return [{"query": queries[0], "index": "create index on users(email)", "method": method}]


def run_dispatch(argv: list[str], adapter: FakeAdapter | None = None) -> tuple[str, FakeAdapter]:
    fake = adapter or FakeAdapter()
    args = _parse(argv)
    return asyncio.run(dispatch(args, fake)), fake


def _parse(argv: list[str]) -> argparse.Namespace:
    args = build_parser().parse_args(argv)
    if args.command is None:
        args.command = "dashboard"
    validate_args(args)
    return args


def test_dispatch_dashboard_renders_schemas_extensions_and_help() -> None:
    output, adapter = run_dispatch([])

    assert "bin: postgres-axi" in output
    assert "schemas[2]{schema}:" in output
    assert "extensions[1]{schema,name,type}:" in output
    assert "Run `postgres-axi indexes workload`" in output
    assert adapter.calls == [
        ("list_schemas", ()),
        ("list_objects", ("public", "extension")),
    ]


def test_dispatch_schemas() -> None:
    output, adapter = run_dispatch(["schemas"])

    assert "schemas[2]{schema}:" in output
    assert "public" in output
    assert "Run `postgres-axi health --type all`" in output
    assert adapter.calls == [("list_schemas", ())]


def test_dispatch_objects() -> None:
    output, adapter = run_dispatch(["objects", "app", "--type", "view"])

    assert "objects[3]{schema,name,type}:" in output
    assert "app,users,view" in output
    assert "app,app_users,view" in output
    assert "app,audit_log,view" in output
    assert "Run `postgres-axi describe app.<name> --type view`" in output
    assert adapter.calls == [("list_objects", ("app", "view"))]


def test_dispatch_objects_filters_client_side() -> None:
    output, adapter = run_dispatch(["objects", "app", "--type", "table", "--filter", "user"])

    assert "objects[2]{schema,name,type}:" in output
    assert "app,users,table" in output
    assert "app,app_users,table" in output
    assert "audit_log" not in output
    assert adapter.calls == [("list_objects", ("app", "table"))]


def test_dispatch_objects_filters_by_prefix_client_side() -> None:
    output, adapter = run_dispatch(["objects", "app", "--type", "table", "--prefix", "app_"])

    assert "objects[1]{schema,name,type}:" in output
    assert "app,app_users,table" in output
    assert "audit_log" not in output
    assert adapter.calls == [("list_objects", ("app", "table"))]


def test_dispatch_objects_combines_filter_and_prefix_client_side() -> None:
    output, adapter = run_dispatch(["objects", "app", "--type", "table", "--filter", "users", "--prefix", "app_"])

    assert "objects[1]{schema,name,type}:" in output
    assert "app,app_users,table" in output
    assert "app,users,table" not in output
    assert "audit_log" not in output
    assert adapter.calls == [("list_objects", ("app", "table"))]


def test_dispatch_describe() -> None:
    output, adapter = run_dispatch(["describe", "app.users"])

    assert "object:" in output
    assert "schema: app" in output
    assert "columns[1]{name,type}:" in output
    assert "Run `postgres-axi inspect app.users`" in output
    assert 'Run `postgres-axi sql "select count(*) from app.users"`' in output
    assert 'Run `postgres-axi explain "select count(*) from app.users"`' in output
    assert "select * from app.users" not in output
    assert adapter.calls == [
        ("validate_object_metadata", ("app", "users", "table")),
        ("get_object_details", ("app", "users", "table")),
    ]


def test_dispatch_describe_missing_object_raises_not_found() -> None:
    adapter = FakeAdapter()

    try:
        run_dispatch(["describe", "app.missing"], adapter)
    except AxiError as exc:
        assert exc.code == "object_not_found"
    else:
        raise AssertionError("expected object_not_found")

    assert adapter.calls == [("validate_object_metadata", ("app", "missing", "table"))]


def test_dispatch_describe_type_mismatch_raises() -> None:
    adapter = FakeAdapter()

    try:
        run_dispatch(["describe", "app.actual_view"], adapter)
    except AxiError as exc:
        assert exc.code == "object_type_mismatch"
        assert exc.hint == "Use --type view."
    else:
        raise AssertionError("expected object_type_mismatch")

    assert adapter.calls == [("validate_object_metadata", ("app", "actual_view", "table"))]


def test_dispatch_sql() -> None:
    output, adapter = run_dispatch(["sql", "select 1"])

    assert "rows[1]{id,email}:" in output
    assert "1,a@example.test" in output
    assert adapter.calls == [("execute_sql", ("select 1",))]


def test_dispatch_explain_with_hypothetical_indexes() -> None:
    query = "select * from users where email = 'a@example.test'"
    indexes = '[{"table": "users", "columns": ["email"], "name": "idx_users_email"}]'
    output, adapter = run_dispatch(["explain", query, "--hypothetical-indexes", indexes])
    quoted_query = "'select * from users where email = '\\''a@example.test'\\'''"

    assert "plan: |" in output
    assert "Index Scan using idx_users_email" in output
    assert "Hypothetical Indexes: idx_users_email" in output
    assert f"Run `postgres-axi diagnose {quoted_query}`" in output
    assert adapter.calls == [
        (
            "explain_query",
            (
                query,
                False,
                [{"table": "users", "columns": ["email"], "name": "idx_users_email"}],
            ),
        )
    ]


def test_dispatch_top() -> None:
    output, adapter = run_dispatch(["top", "--sort-by", "mean_time", "--limit", "5"])

    assert "queries[1]{query,calls,mean_time}:" in output
    assert "select 1,7,1.25" in output
    assert "Run `postgres-axi indexes workload`" in output
    assert adapter.calls == [("get_top_queries", ("mean_time", 5))]


def test_dispatch_top_resources_falls_back_to_total_time_on_division_by_zero() -> None:
    output, adapter = run_dispatch(["top", "--sort-by", "resources", "--limit", "5"])

    assert "queries[1]{query,calls,mean_time}:" in output
    assert "fell back to `--sort-by total_time`" in output
    assert adapter.calls == [
        ("get_top_queries", ("resources", 5)),
        ("get_top_queries", ("total_time", 5)),
    ]


def test_dispatch_health() -> None:
    output, adapter = run_dispatch(["health", "--type", "indexes"])

    assert "health:" in output
    assert "status: ok" in output
    assert "checks[1]{name,status}:" in output
    assert adapter.calls == [("analyze_db_health", ("indexes",))]


def test_dispatch_indexes_workload() -> None:
    output, adapter = run_dispatch(["indexes", "workload", "--max-index-size-mb", "256", "--method", "llm"])

    assert "index_recommendations[1]{table,columns,method}:" in output
    assert "users,['email'],llm" in output
    assert adapter.calls == [("analyze_workload_indexes", (256, "llm"))]


def test_dispatch_indexes_queries() -> None:
    query = "select * from users where email = 'a@example.test'"
    output, adapter = run_dispatch(["indexes", "queries", query, "--max-index-size-mb", "128"])

    assert "index_recommendations[1]{query,index,method}:" in output
    assert "create index on users(email)" in output
    assert adapter.calls == [("analyze_query_indexes", ([query], 128, "dta"))]


def test_dispatch_inspect() -> None:
    output, adapter = run_dispatch(["inspect", "app.users"])

    assert "object:" in output
    assert "name: users" in output
    assert 'Run `postgres-axi sql "select count(*) from app.users"`' in output
    assert 'Run `postgres-axi explain "select count(*) from app.users"`' in output
    assert 'Run `postgres-axi indexes queries "select 1 from app.users where <predicate>"`' in output
    assert "select * from app.users" not in output
    assert adapter.calls == [
        ("validate_object_metadata", ("app", "users", "table")),
        ("get_object_details", ("app", "users", "table")),
    ]


def test_dispatch_diagnose_preserves_plan_when_index_analysis_fails() -> None:
    query = "select * from users where email = 'a@example.test'"
    output, adapter = run_dispatch(
        ["diagnose", query, "--max-index-size-mb", "64", "--method", "llm"],
        FakeAdapter(fail_query_indexes=True),
    )

    assert "plan: |" in output
    assert "Seq Scan on users" in output
    assert "error:" in output
    assert "code: index_analysis_failed" in output
    assert "message: HypoPG is unavailable." in output
    assert adapter.calls == [
        ("explain_query", (query, False, [])),
        ("analyze_query_indexes", ([query], 64, "llm")),
    ]
