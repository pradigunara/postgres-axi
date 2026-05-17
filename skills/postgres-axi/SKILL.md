---
name: postgres-axi
description: Use postgres-axi for PostgreSQL inspection, schema exploration, query execution, EXPLAIN plans, health checks, pg_stat_statements analysis, and index recommendation workflows when an agent should prefer compact AXI-style CLI output over verbose MCP schemas or ad hoc SQL. Trigger when the user asks to inspect a Postgres database, diagnose a slow query, review tables/indexes/constraints, check database health, or summarize database state from the configured DATABASE_URI.
---

# Postgres AXI

## Core Rule

Prefer `postgres-axi` over raw `psql` or direct `postgres-mcp` calls when the task can be answered by its command surface. It is designed for agent workflows: compact output, explicit truncation, structured errors, and next-step hints.

`postgres-axi` defaults to restricted mode. Use unrestricted mode only when the
user explicitly needs write-capable SQL:

```bash
postgres-axi --access-mode unrestricted ...
```

## Setup Checks

Before database work, verify the CLI is available and configuration exists:

```bash
postgres-axi
```

If the command returns `missing_database_uri`, ask for or set `DATABASE_URI`. If it returns `missing_dependency`, install or expose `postgres-mcp` in the current environment before continuing.

Do not paste credentials into the final response. If a command output includes a connection string or secret, summarize it without the secret.

## Command Selection

Use these commands for common tasks:

```bash
postgres-axi
postgres-axi schemas
postgres-axi objects public --type table
postgres-axi describe public.users
postgres-axi inspect public.users
postgres-axi sql "select * from public.users limit 20"
postgres-axi explain "select * from public.users where id = 1"
postgres-axi diagnose "select * from public.users where email = 'a@example.com'"
postgres-axi top --sort-by resources --limit 10
postgres-axi health --type all
postgres-axi indexes workload
postgres-axi indexes queries "select * from public.users where email = 'a@example.com'"
```

Use `inspect` when the user asks about a specific table or view. It combines details with next commands.

Use `diagnose` when the user gives a query and asks about performance. It combines an explain plan with index recommendations.

Use `top` before `indexes workload` when the user asks generally what to optimize. `top` identifies the candidate workload; `indexes workload` recommends indexes.

## Output Handling

Treat AXI output as already summarized. Preserve important rows, counts, warnings, and `help[...]` hints, but avoid expanding compact output into verbose JSON unless the user asks.

If output is truncated, rerun with `--limit N` or `--full` only when the missing rows are necessary for the decision:

```bash
postgres-axi --limit 100 objects public --type table
postgres-axi --full explain "select ..."
```

## Safety

Default to read-only behavior:

```bash
postgres-axi --access-mode restricted sql "select ..."
```

Do not run data-changing SQL unless the user explicitly asks for it and the risk is clear. For destructive SQL, explain the intended statement first and wait for user confirmation unless they already gave exact SQL and explicit permission to execute it.

Prefer `explain` without `--analyze` first. Use `--analyze` only when executing the query is safe and the user needs runtime evidence.

## Reporting

When reporting results, include:

- the command run
- the relevant findings
- any truncation or missing-extension caveats
- the next command if more evidence is needed

For performance findings, distinguish evidence from recommendation. Example: “The plan shows a sequential scan on `public.users`; `indexes queries` recommends an index on `email`.”
