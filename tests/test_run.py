import asyncio
from typing import Any

import pytest

from postgres_axi import cli
from postgres_axi.format import AxiError


class FakeMcpApiAdapter:
    instances: list["FakeMcpApiAdapter"] = []
    enter_error: AxiError | None = None
    enter_delay: float = 0.0

    def __init__(self, database_url: str | None, access_mode: str) -> None:
        self.database_url = database_url
        self.access_mode = access_mode
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.__class__.instances.append(self)

    def connect(self) -> "FakeMcpApiAdapter":
        return self

    async def __aenter__(self) -> "FakeMcpApiAdapter":
        if self.enter_delay:
            await asyncio.sleep(self.enter_delay)
        if self.enter_error is not None:
            raise self.enter_error
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def list_schemas(self) -> list[dict[str, str]]:
        self.calls.append(("list_schemas", ()))
        return [{"schema_name": "public"}]

    async def list_objects(self, schema_name: str, object_type: str) -> list[dict[str, str]]:
        self.calls.append(("list_objects", (schema_name, object_type)))
        return [{"object_name": "pg_stat_statements", "object_type": object_type}]

    async def explain_query(
        self,
        sql: str,
        analyze: bool,
        hypothetical_indexes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.calls.append(("explain_query", (sql, analyze, hypothetical_indexes)))
        return {"sql": sql, "analyze": analyze, "hypothetical_indexes": hypothetical_indexes}


@pytest.fixture
def fake_adapter(monkeypatch: pytest.MonkeyPatch) -> type[FakeMcpApiAdapter]:
    FakeMcpApiAdapter.instances = []
    FakeMcpApiAdapter.enter_error = None
    FakeMcpApiAdapter.enter_delay = 0.0
    monkeypatch.setattr(cli, "McpApiAdapter", FakeMcpApiAdapter)
    return FakeMcpApiAdapter


def run_cli(argv: list[str] | None = None) -> int:
    return asyncio.run(cli.run(argv))


def test_no_args_runs_dashboard_successfully(
    fake_adapter: type[FakeMcpApiAdapter],
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = run_cli([])

    assert code == 0
    assert fake_adapter.instances[0].calls == [
        ("list_schemas", ()),
        ("list_objects", ("public", "extension")),
    ]
    output = capsys.readouterr().out
    assert output.startswith("bin: ")
    assert "postgres-axi" in output.splitlines()[0]
    assert "schemas[1]" in output
    assert "extensions[1]" in output


def test_default_access_mode_passed_to_adapter_is_restricted(
    fake_adapter: type[FakeMcpApiAdapter],
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = run_cli([])

    assert code == 0
    assert fake_adapter.instances[0].access_mode == "restricted"
    capsys.readouterr()


def test_unrestricted_access_mode_passed_to_adapter(
    fake_adapter: type[FakeMcpApiAdapter],
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = run_cli(["--access-mode", "unrestricted"])

    assert code == 0
    assert fake_adapter.instances[0].access_mode == "unrestricted"
    capsys.readouterr()


def test_missing_database_error_renders_structured_output_and_returns_1(
    fake_adapter: type[FakeMcpApiAdapter],
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_adapter.enter_error = AxiError(
        code="missing_database_uri",
        message="No database URL provided.",
        hint="Set DATABASE_URI or pass --database-url postgresql://...",
    )

    code = run_cli([])

    assert code == 1
    assert capsys.readouterr().out == (
        "error:\n"
        "  code: missing_database_uri\n"
        "  message: No database URL provided.\n"
        "help[1]:\n"
        "  Set DATABASE_URI or pass --database-url postgresql://...\n"
    )


def test_invalid_hypothetical_indexes_json_renders_invalid_json_and_returns_1(
    fake_adapter: type[FakeMcpApiAdapter],
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = run_cli(["explain", "select 1", "--hypothetical-indexes", "{not json"])

    assert code == 1
    output = capsys.readouterr().out
    assert output.startswith("error:\n  code: invalid_json\n  message: ")
    assert "Expecting property name enclosed in double quotes" in output
    assert fake_adapter.instances == []


def test_parser_usage_errors_are_structured_stdout(
    fake_adapter: type[FakeMcpApiAdapter],
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = run_cli(["objects", "--type", "invalid"])

    assert code == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "error:\n  code: usage_error\n" in captured.out
    assert "invalid choice" in captured.out
    assert fake_adapter.instances == []


def test_integrations_do_not_require_database_connection(
    fake_adapter: type[FakeMcpApiAdapter],
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = run_cli(["integrations", "--app", "codex"])

    assert code == 0
    output = capsys.readouterr().out
    assert "integrations[1]{app,target,event,command}:" in output
    assert "codex,~/.codex/hooks.json,SessionStart" in output
    assert fake_adapter.instances == []


def test_database_timeout_renders_structured_output(
    fake_adapter: type[FakeMcpApiAdapter],
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_adapter.enter_delay = 0.05

    code = run_cli(["--timeout", "0.001", "schemas"])

    assert code == 1
    output = capsys.readouterr().out
    assert "error:\n  code: timeout\n" in output
    assert "Operation exceeded 0.001s." in output


def test_invalid_timeout_renders_structured_output(
    fake_adapter: type[FakeMcpApiAdapter],
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = run_cli(["--timeout", "0", "schemas"])

    assert code == 1
    output = capsys.readouterr().out
    assert "error:\n  code: invalid_timeout\n" in output
    assert fake_adapter.instances == []
