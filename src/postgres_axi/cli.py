from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import json
import os
import signal
import shutil
import sys
from collections.abc import Iterator
from typing import Any

from .adapter import McpApiAdapter
from .format import AxiError, DEFAULT_LIMIT, render_error, render_value


def main() -> None:
    timeout = command_timeout(sys.argv[1:])
    try:
        with operation_timeout(timeout):
            raise SystemExit(asyncio.run(run()))
    except OperationTimeout:
        print(
            render_error(
                AxiError(
                    code="timeout",
                    message=f"Operation exceeded {timeout:g}s.",
                    hint="Retry with --timeout <seconds> or verify database connectivity.",
                )
            )
        )
        raise SystemExit(1)


async def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except AxiError as exc:
        print(render_error(exc))
        return 2
    if args.command is None:
        args.command = "dashboard"

    try:
        validate_args(args)
        if args.command == "integrations":
            print(render_integrations(args.app))
            return 0
        try:
            async with asyncio.timeout(args.timeout):
                async with McpApiAdapter(args.database_url, args.access_mode).connect() as adapter:
                    output = await dispatch(args, adapter)
        except TimeoutError as exc:
            raise AxiError(
                code="timeout",
                message=f"Operation exceeded {args.timeout:g}s.",
                hint="Retry with --timeout <seconds> or verify database connectivity.",
            ) from exc
    except AxiError as exc:
        print(render_error(exc))
        return 1

    print(output)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = AxiArgumentParser(
        prog="postgres-axi",
        description="AXI-style CLI facade for postgres-mcp",
        examples=[
            "postgres-axi",
            "postgres-axi objects public --type table --fields schema,name,type",
            'postgres-axi diagnose "select * from public.users where id = 1"',
        ],
    )
    parser.add_argument("--database-url", help="PostgreSQL connection URL. Defaults to DATABASE_URI.")
    parser.add_argument(
        "--access-mode",
        choices=["restricted", "unrestricted"],
        default="restricted",
        help="Use restricted for read-only access. Defaults to restricted.",
    )
    parser.add_argument("--limit", type=int, default=argparse.SUPPRESS, help="Max rows or text chunks to show.")
    parser.add_argument("--fields", default=argparse.SUPPRESS, help="Comma-separated output fields to show.")
    parser.add_argument("--timeout", type=float, default=30.0, help="Seconds before a database operation times out.")
    parser.add_argument("--no-redact", action="store_true", default=argparse.SUPPRESS, help="Show sensitive fields.")
    parser.add_argument(
        "--full",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Disable row, text, and cell truncation.",
    )
    parser.set_defaults(command="dashboard")

    subparsers = parser.add_subparsers(dest="command", parser_class=AxiArgumentParser)

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
    top.add_argument("--timeout", type=float, default=argparse.SUPPRESS)
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

    integrations = subparsers.add_parser("integrations", help="Show session integration snippets.")
    integrations.add_argument("--app", choices=["all", "claude", "codex", "opencode"], default="all")

    return parser


def add_output_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--limit", type=int, default=argparse.SUPPRESS, help="Max rows or text chunks to show.")
    parser.add_argument("--fields", default=argparse.SUPPRESS, help="Comma-separated output fields to show.")
    parser.add_argument(
        "--timeout",
        type=float,
        default=argparse.SUPPRESS,
        help="Seconds before a database operation times out.",
    )
    parser.add_argument("--no-redact", action="store_true", default=argparse.SUPPRESS, help="Show sensitive fields.")
    parser.add_argument(
        "--full",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Disable row, text, and cell truncation.",
    )


def validate_args(args: argparse.Namespace) -> None:
    if not hasattr(args, "limit"):
        args.limit = 10 if args.command == "top" else DEFAULT_LIMIT
    if not hasattr(args, "full"):
        args.full = False
    if not hasattr(args, "no_redact"):
        args.no_redact = False
    if not hasattr(args, "fields"):
        args.fields = None
    elif isinstance(args.fields, str):
        args.fields = parse_fields(args.fields)
    if args.timeout <= 0:
        raise AxiError(
            code="invalid_timeout",
            message="--timeout must be greater than 0.",
            hint="Use --timeout 30",
        )
    if args.command == "explain":
        args.hypothetical_indexes = parse_json_list(args.hypothetical_indexes)


async def dispatch(args: argparse.Namespace, adapter: McpApiAdapter) -> str:
    limit = 10**9 if args.full else args.limit

    if args.command == "dashboard":
        return await dashboard(adapter, limit, args.full)
    if args.command == "schemas":
        return render_value(
            await adapter.list_schemas(),
            name="schemas",
            limit=limit,
            fields=args.fields,
            help=[
                "Run `postgres-axi objects public --type table`",
                "Run `postgres-axi health --type all`",
            ],
            full=args.full,
            redact=not args.no_redact,
        )
    if args.command == "objects":
        objects = await adapter.list_objects(args.schema, args.type)
        return render_value(
            filter_objects(objects, args.filter, args.prefix),
            name="objects",
            limit=limit,
            fields=args.fields,
            empty=f"objects: 0 {args.type}s found in schema {args.schema}",
            help=[
                f"Run `postgres-axi describe {args.schema}.<name> --type {args.type}`",
                f"Run `postgres-axi objects {args.schema} --type {args.type} --prefix <prefix>`",
            ],
            full=args.full,
            redact=not args.no_redact,
        )
    if args.command == "describe":
        schema, name = split_object(args.object)
        await adapter.validate_object_metadata(schema, name, args.type)
        return render_value(
            await adapter.get_object_details(schema, name, args.type),
            name="object",
            limit=limit,
            fields=args.fields,
            help=[
                f"Run `postgres-axi inspect {schema}.{name}`",
                f"Run `postgres-axi sql \"select count(*) from {schema}.{name}\"`",
                f"Run `postgres-axi explain \"select count(*) from {schema}.{name}\"`",
            ],
            full=args.full,
            redact=not args.no_redact,
        )
    if args.command == "sql":
        return render_value(
            await adapter.execute_sql(args.query),
            name="rows",
            limit=limit,
            fields=args.fields,
            empty="rows: 0 rows returned",
            full=args.full,
            default_all_fields=True,
            redact=not args.no_redact,
        )
    if args.command == "explain":
        return render_value(
            await adapter.explain_query(args.query, args.analyze, args.hypothetical_indexes),
            name="plan",
            limit=limit,
            fields=args.fields,
            help=[f"Run `postgres-axi diagnose {shell_quote(args.query)}`"],
            full=args.full,
            redact=not args.no_redact,
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
                fields=args.fields,
                empty="queries: 0 pg_stat_statements entries found",
                help=[
                    "`--sort-by resources` failed with division by zero; fell back to `--sort-by total_time`",
                    "Run `postgres-axi top --sort-by mean_time --limit 10`",
                ],
                full=args.full,
                redact=not args.no_redact,
            )
        return render_value(
            queries,
            name="queries",
            limit=args.limit,
            fields=args.fields,
            empty="queries: 0 pg_stat_statements entries found",
            help=["Run `postgres-axi indexes workload`"],
            full=args.full,
            redact=not args.no_redact,
        )
    if args.command == "health":
        return render_value(
            await adapter.analyze_db_health(args.type),
            name="health",
            limit=limit,
            fields=args.fields,
            full=args.full,
            redact=not args.no_redact,
        )
    if args.command == "indexes":
        return await dispatch_indexes(args, adapter, limit, args.full, not args.no_redact)
    if args.command == "inspect":
        schema, name = split_object(args.object)
        return await inspect_object(adapter, schema, name, args.type, limit, args.full, args.fields, not args.no_redact)
    if args.command == "diagnose":
        return await diagnose_query(
            adapter,
            args.query,
            args.max_index_size_mb,
            args.method,
            limit,
            args.full,
            not args.no_redact,
        )

    raise AxiError(code="unknown_command", message=f"Unsupported command: {args.command}")


async def dashboard(adapter: McpApiAdapter, limit: int, full: bool) -> str:
    schemas = await adapter.list_schemas()
    extensions = await adapter.list_objects("public", "extension")
    parts = [
        f"bin: {display_executable()}",
        "description: Inspect and tune the configured PostgreSQL database",
        render_value(schemas, name="schemas", limit=min(limit, 8), full=full),
        render_value(extensions, name="extensions", limit=min(limit, 8), full=full),
        "help[5]:",
        "  Run `postgres-axi schemas`",
        "  Run `postgres-axi objects public --type table`",
        "  Run `postgres-axi health --type all`",
        "  Run `postgres-axi top --limit 10`",
        "  Run `postgres-axi indexes workload`",
    ]
    return "\n".join(parts)


async def dispatch_indexes(
    args: argparse.Namespace,
    adapter: McpApiAdapter,
    limit: int,
    full: bool,
    redact: bool,
) -> str:
    if args.index_command == "workload":
        result = await adapter.analyze_workload_indexes(args.max_index_size_mb, args.method)
        return render_value(
            result,
            name="index_recommendations",
            limit=limit,
            fields=args.fields,
            empty="index_recommendations: 0 workload index recommendations found",
            full=full,
            redact=redact,
        )
    if args.index_command == "queries":
        result = await adapter.analyze_query_indexes(args.queries, args.max_index_size_mb, args.method)
        return render_value(
            result,
            name="index_recommendations",
            limit=limit,
            fields=args.fields,
            empty="index_recommendations: 0 query index recommendations found",
            full=full,
            redact=redact,
        )
    raise AxiError(code="unknown_index_command", message=f"Unsupported indexes command: {args.index_command}")


async def inspect_object(
    adapter: McpApiAdapter,
    schema: str,
    name: str,
    object_type: str,
    limit: int,
    full: bool,
    fields: list[str] | None,
    redact: bool,
) -> str:
    await adapter.validate_object_metadata(schema, name, object_type)
    details = await adapter.get_object_details(schema, name, object_type)
    return render_value(
        details,
        name="object",
        limit=limit,
        fields=fields,
        help=[
            f"Run `postgres-axi sql \"select count(*) from {schema}.{name}\"`",
            f"Run `postgres-axi explain \"select count(*) from {schema}.{name}\"`",
            f"Run `postgres-axi indexes queries \"select 1 from {schema}.{name} where <predicate>\"`",
        ],
        full=full,
        redact=redact,
    )


async def diagnose_query(
    adapter: McpApiAdapter,
    query: str,
    max_index_size_mb: int,
    method: str,
    limit: int,
    full: bool,
    redact: bool,
) -> str:
    plan = await adapter.explain_query(query, False, [])
    try:
        indexes_output = render_value(
            await adapter.analyze_query_indexes([query], max_index_size_mb, method),
            name="index_recommendations",
            limit=limit,
            full=full,
            redact=redact,
        )
    except AxiError as exc:
        indexes_output = render_error(exc)
    return "\n".join(
        [
            render_value(plan, name="plan", limit=limit, full=full, redact=redact),
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


def parse_fields(raw: str) -> list[str]:
    fields = [field.strip() for field in raw.split(",") if field.strip()]
    if not fields:
        raise AxiError(
            code="invalid_fields",
            message="--fields must include at least one field name.",
            hint="Use --fields schema,name,type",
        )
    return fields


def command_timeout(argv: list[str]) -> float:
    for index, value in enumerate(argv):
        if value == "--timeout" and index + 1 < len(argv):
            return parse_timeout_value(argv[index + 1])
        if value.startswith("--timeout="):
            return parse_timeout_value(value.split("=", 1)[1])
    return 30.0


def parse_timeout_value(raw: str) -> float:
    try:
        timeout = float(raw)
    except ValueError:
        return 30.0
    return timeout if timeout > 0 else 30.0


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def display_executable() -> str:
    argv0 = sys.argv[0] if sys.argv else "postgres-axi"
    command = "postgres-axi"
    resolved = shutil.which(command) or shutil.which(argv0) or argv0
    if not os.path.isabs(resolved):
        return resolved
    home = os.path.expanduser("~")
    if resolved == home:
        return "~"
    if resolved.startswith(home + os.sep):
        return "~" + resolved[len(home) :]
    return resolved


class OperationTimeout(Exception):
    pass


@contextmanager
def operation_timeout(seconds: float) -> Iterator[None]:
    if not hasattr(signal, "SIGALRM"):
        yield
        return

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, 0)

    def raise_timeout(signum: int, frame: object) -> None:
        raise OperationTimeout()

    signal.signal(signal.SIGALRM, raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        signal.signal(signal.SIGALRM, previous_handler)


def render_integrations(app: str) -> str:
    command = f"{display_executable()} --limit 8"
    snippets = []
    if app in ("all", "codex"):
        snippets.append(
            {
                "app": "codex",
                "target": "~/.codex/hooks.json",
                "event": "SessionStart",
                "command": command,
            }
        )
    if app in ("all", "claude"):
        snippets.append(
            {
                "app": "claude",
                "target": "~/.claude/settings.json",
                "event": "SessionStart",
                "command": command,
            }
        )
    if app in ("all", "opencode"):
        snippets.append(
            {
                "app": "opencode",
                "target": "~/.config/opencode/plugins/postgres-axi",
                "event": "system-context",
                "command": command,
            }
        )
    return "\n".join(
        [
            f"bin: {display_executable()}",
            "description: Session integration targets for ambient PostgreSQL context",
            render_value(snippets, name="integrations", limit=10),
            "help[1]:",
            "  Add the relevant snippet to your agent config so `postgres-axi` runs at session start",
        ]
    )


class AxiArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args: Any, examples: list[str] | None = None, **kwargs: Any) -> None:
        self.examples = examples or []
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> None:
        raise AxiError(
            code="usage_error",
            message=message,
            hint=f"Run `{self.prog} --help`",
        )

    def format_help(self) -> str:
        lines = [
            f"command: {self.prog}",
            f"description: {self.description or ''}",
        ]
        actions = self._actions
        options = []
        commands = []
        for action in actions:
            if isinstance(action, argparse._SubParsersAction):
                help_by_name = {choice.dest: choice.help for choice in action._choices_actions}
                for name in action.choices:
                    commands.append({"name": name, "description": help_by_name.get(name, "")})
                continue
            if not action.option_strings and not action.metavar:
                continue
            label = ",".join(action.option_strings) if action.option_strings else str(action.metavar)
            if action.default not in (None, argparse.SUPPRESS) and action.default is not False:
                label = f"{label} default={action.default}"
            options.append({"flag": label, "description": action.help or ""})
        lines.extend(render_value(options, name="options", limit=100).splitlines())
        if commands:
            lines.extend(render_value(commands, name="commands", limit=100).splitlines())
        if self.examples:
            lines.append(f"examples[{len(self.examples)}]:")
            lines.extend(f"  {example}" for example in self.examples)
        return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
