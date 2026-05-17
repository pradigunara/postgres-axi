from postgres_axi.format import AxiError, render_error, render_value, unwrap_mcp_text


class TextContent:
    def __init__(self, text: str) -> None:
        self.text = text


def test_unwrap_mcp_text_parses_python_literals() -> None:
    assert unwrap_mcp_text([TextContent("[{'schema': 'public', 'name': 'users'}]")]) == [
        {"schema": "public", "name": "users"}
    ]


def test_render_rows_uses_compact_field_header() -> None:
    output = render_value(
        [{"schema": "public", "name": "users", "type": "BASE TABLE", "extra": "ignored"}],
        name="objects",
    )

    assert "objects[1]{schema,name,type,extra}:" in output
    assert "public,users,BASE TABLE,ignored" in output


def test_render_rows_truncates() -> None:
    output = render_value([{"id": i} for i in range(3)], name="rows", limit=2)

    assert "rows[2 of 3]{id}:" in output
    assert "note: truncated, use --limit 3, --full, or narrower filters" in output


def test_render_error_is_structured() -> None:
    output = render_error(AxiError(code="missing_database_uri", message="No database URL.", hint="Set DATABASE_URI."))

    assert "error:" in output
    assert "code: missing_database_uri" in output
    assert "help[1]:" in output


def test_render_mapping_error_is_structured_and_hides_trace() -> None:
    output = render_value(
        {"error": "HypoPG is not available.\nInstall the extension.", "_langfuse_trace": []},
        name="index_recommendations",
    )

    assert "index_recommendations:" in output
    assert "status: unavailable" in output
    assert "error: HypoPG is not available." in output
    assert "**" not in output
    assert "Install the extension." not in output
    assert "_langfuse_trace" not in output


def test_render_mapping_error_keeps_non_hypopg_errors_structured() -> None:
    output = render_value(
        {"error": "Connection failed.\nTrace details.", "_langfuse_trace": []},
        name="rows",
    )

    assert "rows:" in output
    assert "code: upstream_error" in output
    assert "message: |" in output
    assert "Connection failed." in output
    assert "_langfuse_trace" not in output


def test_render_text_blob_with_python_rows_as_structured_rows() -> None:
    output = render_value(
        "Top queries:\n[{'query': 'select 1', 'calls': 7, 'mean_time': 1.25}]",
        name="queries",
    )

    assert "queries[1]{query,calls,mean_time}:" in output
    assert "select 1,7,1.25" in output
    assert "Top queries:" not in output


def test_render_insufficient_privilege_query_text_adds_note() -> None:
    output = render_value(
        [{"query": "<insufficient privilege>", "calls": 10, "mean_time": 2.5}],
        name="queries",
    )

    assert "<insufficient privilege>,10,2.5" in output
    assert "note: query text is hidden by PostgreSQL privileges" in output


def test_render_rows_truncates_long_cells() -> None:
    output = render_value([{"query": "select " + "x" * 300, "calls": 1}], name="queries")

    assert "…" in output
    assert "x" * 250 not in output


def test_render_text_truncates_on_line_boundaries() -> None:
    output = render_value("first line\n" + ("second line is long " * 20) + "\nthird line", name="health", limit=1)

    assert "first line" in output
    assert "second line is long" not in output
    assert "note: truncated at 240 chars" in output
