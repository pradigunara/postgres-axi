# postgres-axi

`postgres-axi` is an AXI-style CLI facade for
[`crystaldba/postgres-mcp`](https://github.com/crystaldba/postgres-mcp).

The goal is to keep the existing MCP server intact while exposing the same
PostgreSQL inspection and tuning surface through a compact, agent-friendly CLI:

- small default outputs
- TOON-like rows instead of verbose JSON
- explicit truncation
- structured errors on stdout
- useful no-argument dashboard
- contextual next commands

## Status

Initial scaffold. The CLI imports and calls the upstream `postgres_mcp.server`
tool functions directly, so runtime behavior tracks the installed
`postgres-mcp` package.

## Install CLI

After this repository is published to GitHub:

```bash
uv tool install "git+https://github.com/pradigunara/postgres-axi.git"
```

Upgrade later with:

```bash
uv tool upgrade postgres-axi
```

For local development from a checkout:

```bash
uv pip install -e .
```

`postgres-mcp` must be installable in the same environment.

## Install Skill

The Codex skill lives in `skills/postgres-axi`.

Preferred install target:

```text
~/.agents/skills
```

Install it from a checkout:

```bash
mkdir -p "$HOME/.agents/skills"
cp -R skills/postgres-axi "$HOME/.agents/skills/postgres-axi"
```

Or install directly from GitHub once published:

```bash
tmpdir="$(mktemp -d)"
git clone --depth 1 https://github.com/pradigunara/postgres-axi.git "$tmpdir/postgres-axi"
mkdir -p "$HOME/.agents/skills"
cp -R "$tmpdir/postgres-axi/skills/postgres-axi" "$HOME/.agents/skills/postgres-axi"
rm -rf "$tmpdir"
```

Restart your agent runtime after installing the skill so it is discovered.

If your runtime only scans `${CODEX_HOME:-$HOME/.codex}/skills`, symlink the
installed skill:

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
ln -s "$HOME/.agents/skills/postgres-axi" "${CODEX_HOME:-$HOME/.codex}/skills/postgres-axi"
```

## Configuration

Set the same database URL used by `postgres-mcp`:

```bash
export DATABASE_URI='postgresql://user:password@localhost:5432/dbname'
```

Access is restricted by default. This blocks write-capable SQL unless you
explicitly opt into unrestricted mode:

```bash
postgres-axi sql "select count(*) from public.users"
postgres-axi --access-mode unrestricted sql "create table public.example(id int)"
```

## Commands

```bash
postgres-axi
postgres-axi schemas
postgres-axi objects public --type table
postgres-axi describe public.users
postgres-axi sql "select * from public.users limit 5"
postgres-axi explain "select * from public.users where id = 1"
postgres-axi top --sort-by resources --limit 10
postgres-axi health --type all
postgres-axi indexes workload
postgres-axi indexes queries "select * from public.users where email = 'a@example.com'"
postgres-axi inspect public.users
postgres-axi diagnose "select * from public.users where email = 'a@example.com'"
```

## Design

This project intentionally wraps the MCP API surface instead of reimplementing
PostgreSQL logic. The adapter initializes `postgres_mcp.server.db_connection`,
sets `current_access_mode`, calls the upstream async tool functions, and closes
the connection pool on exit.
