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
    assert "note: truncated" in output


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
    assert "code: upstream_error" in output
    assert "message: |" in output
    assert "HypoPG is not available." in output
    assert "_langfuse_trace" not in output
