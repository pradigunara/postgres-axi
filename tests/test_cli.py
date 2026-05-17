import argparse
import pytest

from postgres_axi.cli import build_parser, parse_json_list, shell_quote, split_object
from postgres_axi.format import AxiError


def test_split_object_defaults_to_public_schema() -> None:
    assert split_object("users") == ("public", "users")


def test_parser_allows_no_arg_dashboard() -> None:
    args = build_parser().parse_args([])

    assert args.command is None


def test_split_object_accepts_explicit_schema() -> None:
    assert split_object("private.users") == ("private", "users")


def test_split_object_rejects_empty_segments() -> None:
    with pytest.raises(AxiError) as exc:
        split_object("public.")

    assert exc.value.code == "invalid_object_name"


def test_parse_json_list_accepts_index_specs() -> None:
    assert parse_json_list('[{"table": "users", "columns": ["email"]}]') == [
        {"table": "users", "columns": ["email"]}
    ]


def test_parse_json_list_rejects_non_list() -> None:
    with pytest.raises(AxiError) as exc:
        parse_json_list('{"table": "users"}')

    assert exc.value.code == "invalid_json"


def test_shell_quote_handles_single_quotes() -> None:
    assert shell_quote("select 'x'") == "'select '\\''x'\\'''"
