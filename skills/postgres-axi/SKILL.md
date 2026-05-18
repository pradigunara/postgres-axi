---
name: postgres-axi
description: Use postgres-axi for compact PostgreSQL inspection, SQL execution, EXPLAIN plans, health checks, pg_stat_statements analysis, and index recommendations from the configured DATABASE_URI.
---

# Postgres AXI

Prefer `postgres-axi` over raw `psql` or direct `postgres-mcp` calls when its command surface covers the task. Output is compact AXI-style text with structured errors, truncation notes, redaction, and next-step hints.

## Commands

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

Use `inspect` for a specific table/view, `diagnose` for a specific slow query, and `top` before `indexes workload` for broad optimization work.

## Output

- DB commands default to `--timeout 30`; raise it only for expected slow checks.
- `sql` shows all columns returned by the query. Use `--fields` to narrow wide output.
- Discovery/list commands keep compact default fields.
- Use `--limit N` for more rows and `--full` for untruncated long cells.
- Sensitive-looking columns are redacted by default: password/passwd/pwd, secret, token, api_key/apikey, private_key, credential, session, cookie.
- Use `--no-redact` only when the user explicitly needs raw sensitive values and it is safe to show them.

## Safety

Default to restricted, read-only behavior:

```bash
postgres-axi --access-mode restricted sql "select ..."
```

Use `--access-mode unrestricted` only when the user explicitly needs writes. For write probes, prefer temporary tables. If temp tables are unavailable, use a guarded one-row update and immediate revert:

```bash
postgres-axi sql "select id, name from public.users where name is not null limit 1"
postgres-axi --access-mode unrestricted sql "update public.users set name = '<probe>' where id = '<id>' and name = '<previous>' returning id,name"
postgres-axi --access-mode unrestricted sql "update public.users set name = '<previous>' where id = '<id>' and name = '<probe>' returning id,name"
```

Do not run destructive SQL unless the user explicitly requested it and the risk is clear. Prefer `explain` without `--analyze`; use `--analyze` only when executing the query is safe.

## Reporting

Report the command run, relevant rows/findings, truncation or extension caveats, and the next command only when more evidence is needed. Do not include credentials or raw secrets in final responses.
