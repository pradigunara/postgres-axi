from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from .adapter import McpApiAdapter
from .format import AxiError, DEFAULT_LIMIT, render_error, render_value


def main() -> None:
    raise SystemExit(asyncio.run(run()))


async def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        args.command = "dashboard"

    try:
        validate_args(args)
        async with McpApiAdapter(args.database_url, args.access_mode).connect() as adapter:
            output = await dispatch(args, adapter)
    except AxiError as exc:
        print(render_error(exc))
        return 1

    print(output)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="postgres-axi",
        description="AXI-style CLI facade for postgres-mcp",
    )
    parser.add_argument("--database-url", help="PostgreSQL connection URL. Defaults to DATABASE_URI.")
    parser.add_argument(
        "--access-mode",
        choices=["restricted", "unrestricted"],
        default="restricted",
        help="Use restricted for read-only access. Defaults to restricted.",
    )
    parser.add_argument("--limit", type=int, default=argparse.SUPPRESS, help="Max rows or text chunks to show.")
    parser.add_argument("--full", action="store_true", default=argparse.SUPPRESS, help="Disable output truncation.")
    parser.set_defaults(command="dashboard")

    subparsers = parser.add_subparsers(dest="command")

    schemas = subparsers.add_parser("schemas", help="List database schemas.")
    add_output_options(schemas)

    objects = subparsers.add_parser("objects", help="List schema objects.")
    objects.add_argument("schema", nargs="?", default="public")
    objects.add_argument("--type", choices=["table", "view", "sequence", "extension"], default="table")
    objects.add_argument("--filter", help="Show objects whose name contains this text.")
    objects.add_argument("--prefix", help="Show objects whose name starts with this prefix.")
    add_output_options(objects)

    describe = subparsers.add_parser("describe", help="Describe an object, e.g. public.users.")
    describe.add_argument("object")
    describe.add_argument("--type", choices=["table", "view", "sequence", "extension"], default="table")
    add_output_options(describe)

    sql = subparsers.add_parser("sql", help="Execute SQL.")
    sql.add_argument("query")
    add_output_options(sql)

    explain = subparsers.add_parser("explain", help="Explain SQL.")
    explain.add_argument("query")
    explain.add_argument("--analyze", action="store_true")
    explain.add_argument("--hypothetical-indexes", default="[]", help="JSON list of hypothetical indexes.")
    add_output_options(explain)

    top = subparsers.add_parser("top", help="Show top queries from pg_stat_statements.")
    top.add_argument("--sort-by", choices=["resources", "mean_time", "total_time"], default="resources")
    top.add_argument("--limit", type=int, default=argparse.SUPPRESS)
    top.add_argument("--full", action="store_true", default=argparse.SUPPRESS, help="Disable output truncation.")

    health = subparsers.add_parser("health", help="Run database health checks.")
    health.add_argument("--type", default="all")
    add_output_options(health)

    indexes = subparsers.add_parser("indexes", help="Analyze index opportunities.")
    index_subparsers = indexes.add_subparsers(dest="index_command", required=True)
    workload = index_subparsers.add_parser("workload", help="Analyze workload indexes.")
    workload.add_argument("--max-index-size-mb", type=int, default=10000)
    workload.add_argument("--method", choices=["dta", "llm"], default="dta")
    add_output_options(workload)
    queries = index_subparsers.add_parser("queries", help="Analyze indexes for specific queries.")
    queries.add_argument("queries", nargs="+")
    queries.add_argument("--max-index-size-mb", type=int, default=10000)
    queries.add_argument("--method", choices=["dta", "llm"], default="dta")
    add_output_options(queries)

    inspect = subparsers.add_parser("inspect", help="Describe an object with next-step hints.")
    inspect.add_argument("object")
    inspect.add_argument("--type", choices=["table", "view", "sequence", "extension"], default="table")
    add_output_options(inspect)

    diagnose = subparsers.add_parser("diagnose", help="Explain and index-analyze a query.")
    diagnose.add_argument("query")
    diagnose.add_argument("--max-index-size-mb", type=int, default=10000)
    diagnose.add_argument("--method", choices=["dta", "llm"], default="dta")
    add_output_options(diagnose)

    return parser


def add_output_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--limit", type=int, default=argparse.SUPPRESS, help="Max rows or text chunks to show.")
    parser.add_argument("--full", action="store_true", default=argparse.SUPPRESS, help="Disable output truncation.")


def validate_args(args: argparse.Namespace) -> None:
    if not hasattr(args, "limit"):
        args.limit = 10 if args.command == "top" else DEFAULT_LIMIT
    if not hasattr(args, "full"):
        args.full = False
    if args.command == "explain":
        args.hypothetical_indexes = parse_json_list(args.hypothetical_indexes)


async def dispatch(args: argparse.Namespace, adapter: McpApiAdapter) -> str:
    limit = 10**9 if args.full else args.limit

    if args.command == "dashboard":
        return await dashboard(adapter, limit)
    if args.command == "schemas":
        return render_value(
            await adapter.list_schemas(),
            name="schemas",
            limit=limit,
            help=[
                "Run `postgres-axi objects public --type table`",
                "Run `postgres-axi health --type all`",
            ],
        )
    if args.command == "objects":
        objects = await adapter.list_objects(args.schema, args.type)
        return render_value(
            filter_objects(objects, args.filter, args.prefix),
            name="objects",
            limit=limit,
            help=[
                f"Run `postgres-axi describe {args.schema}.<name> --type {args.type}`",
                f"Run `postgres-axi objects {args.schema} --type {args.type} --prefix <prefix>`",
            ],
        )
    if args.command == "describe":
        schema, name = split_object(args.object)
        await adapter.validate_object_metadata(schema, name, args.type)
        return render_value(
            await adapter.get_object_details(schema, name, args.type),
            name="object",
            limit=limit,
            help=[
                f"Run `postgres-axi inspect {schema}.{name}`",
                f"Run `postgres-axi sql \"select count(*) from {schema}.{name}\"`",
                f"Run `postgres-axi explain \"select count(*) from {schema}.{name}\"`",
            ],
        )
    if args.command == "sql":
        return render_value(await adapter.execute_sql(args.query), name="rows", limit=limit)
    if args.command == "explain":
        return render_value(
            await adapter.explain_query(args.query, args.analyze, args.hypothetical_indexes),
            name="plan",
            limit=limit,
            help=[f"Run `postgres-axi diagnose {shell_quote(args.query)}`"],
        )
    if args.command == "top":
        try:
            queries = await adapter.get_top_queries(args.sort_by, args.limit)
        except AxiError as exc:
            if args.sort_by != "resources" or "division by zero" not in exc.message:
                raise
            queries = await adapter.get_top_queries("total_time", args.limit)
            return render_value(
                queries,
                name="queries",
                limit=args.limit,
                help=[
                    "`--sort-by resources` failed with division by zero; fell back to `--sort-by total_time`",
                    "Run `postgres-axi top --sort-by mean_time --limit 10`",
                ],
            )
        return render_value(
            queries,
            name="queries",
            limit=args.limit,
            help=["Run `postgres-axi indexes workload`"],
        )
    if args.command == "health":
        return render_value(await adapter.analyze_db_health(args.type), name="health", limit=limit)
    if args.command == "indexes":
        return await dispatch_indexes(args, adapter, limit)
    if args.command == "inspect":
        schema, name = split_object(args.object)
        return await inspect_object(adapter, schema, name, args.type, limit)
    if args.command == "diagnose":
        return await diagnose_query(adapter, args.query, args.max_index_size_mb, args.method, limit)

    raise AxiError(code="unknown_command", message=f"Unsupported command: {args.command}")


async def dashboard(adapter: McpApiAdapter, limit: int) -> str:
    schemas = await adapter.list_schemas()
    extensions = await adapter.list_objects("public", "extension")
    parts = [
        "bin: postgres-axi",
        "description: Inspect and tune the configured PostgreSQL database",
        render_value(schemas, name="schemas", limit=min(limit, 8)),
        render_value(extensions, name="extensions", limit=min(limit, 8)),
        "help[5]:",
        "  Run `postgres-axi schemas`",
        "  Run `postgres-axi objects public --type table`",
        "  Run `postgres-axi health --type all`",
        "  Run `postgres-axi top --limit 10`",
        "  Run `postgres-axi indexes workload`",
    ]
    return "\n".join(parts)


async def dispatch_indexes(args: argparse.Namespace, adapter: McpApiAdapter, limit: int) -> str:
    if args.index_command == "workload":
        result = await adapter.analyze_workload_indexes(args.max_index_size_mb, args.method)
        return render_value(result, name="index_recommendations", limit=limit)
    if args.index_command == "queries":
        result = await adapter.analyze_query_indexes(args.queries, args.max_index_size_mb, args.method)
        return render_value(result, name="index_recommendations", limit=limit)
    raise AxiError(code="unknown_index_command", message=f"Unsupported indexes command: {args.index_command}")


async def inspect_object(adapter: McpApiAdapter, schema: str, name: str, object_type: str, limit: int) -> str:
    await adapter.validate_object_metadata(schema, name, object_type)
    details = await adapter.get_object_details(schema, name, object_type)
    return render_value(
        details,
        name="object",
        limit=limit,
        help=[
            f"Run `postgres-axi sql \"select count(*) from {schema}.{name}\"`",
            f"Run `postgres-axi explain \"select count(*) from {schema}.{name}\"`",
            f"Run `postgres-axi indexes queries \"select 1 from {schema}.{name} where <predicate>\"`",
        ],
    )


async def diagnose_query(
    adapter: McpApiAdapter,
    query: str,
    max_index_size_mb: int,
    method: str,
    limit: int,
) -> str:
    plan = await adapter.explain_query(query, False, [])
    try:
        indexes_output = render_value(
            await adapter.analyze_query_indexes([query], max_index_size_mb, method),
            name="index_recommendations",
            limit=limit,
        )
    except AxiError as exc:
        indexes_output = render_error(exc)
    return "\n".join(
        [
            render_value(plan, name="plan", limit=limit),
            indexes_output,
        ]
    )


def split_object(raw: str) -> tuple[str, str]:
    if "." not in raw:
        return "public", raw
    schema, name = raw.split(".", 1)
    if not schema or not name:
        raise AxiError(code="invalid_object_name", message="Use object names like public.users.")
    return schema, name


def filter_objects(objects: Any, text_filter: str | None, prefix: str | None) -> Any:
    if not text_filter and not prefix:
        return objects
    if not isinstance(objects, list):
        return objects

    return [obj for obj in objects if object_name_matches(obj, text_filter, prefix)]


def object_name_matches(obj: Any, text_filter: str | None, prefix: str | None) -> bool:
    name = object_name(obj)
    if name is None:
        return True
    normalized_name = name.lower()
    if text_filter and text_filter.lower() not in normalized_name:
        return False
    if prefix and not normalized_name.startswith(prefix.lower()):
        return False
    return True


def object_name(obj: Any) -> str | None:
    if isinstance(obj, dict):
        for key in ("name", "object_name"):
            value = obj.get(key)
            if isinstance(value, str):
                return value
        return None
    return obj if isinstance(obj, str) else None


def parse_json_list(raw: str) -> list[dict[str, Any]]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AxiError(code="invalid_json", message=str(exc)) from exc
    if not isinstance(value, list):
        raise AxiError(code="invalid_json", message="Expected a JSON list.")
    return value


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


if __name__ == "__main__":
    main()
