import pytest

from postgres_axi.cli import (
    DEFAULT_LIMIT,
    build_parser,
    filter_objects,
    parse_json_list,
    shell_quote,
    split_object,
    validate_args,
)
from postgres_axi.format import AxiError


def test_split_object_defaults_to_public_schema() -> None:
    assert split_object("users") == ("public", "users")


def test_parser_allows_no_arg_dashboard() -> None:
    args = build_parser().parse_args([])

    assert args.command is None


def test_limit_works_after_objects_subcommand() -> None:
    args = build_parser().parse_args(["objects", "public", "--type", "table", "--limit", "159"])
    validate_args(args)

    assert args.command == "objects"
    assert args.limit == 159


def test_limit_works_before_objects_subcommand() -> None:
    args = build_parser().parse_args(["--limit", "159", "objects", "public", "--type", "table"])
    validate_args(args)

    assert args.command == "objects"
    assert args.limit == 159


def test_objects_accepts_filter_and_prefix() -> None:
    args = build_parser().parse_args(["objects", "public", "--filter", "user", "--prefix", "app_"])
    validate_args(args)

    assert args.filter == "user"
    assert args.prefix == "app_"


def test_filter_objects_accepts_object_name_payloads() -> None:
    objects = [
        {"schema": "app", "object_name": "app_users", "object_type": "table"},
        {"schema": "app", "object_name": "audit_log", "object_type": "table"},
    ]

    assert filter_objects(objects, "user", "app_") == [objects[0]]


def test_filter_objects_is_case_insensitive() -> None:
    objects = [
        {"schema": "app", "name": "Card_Balances", "type": "table"},
        {"schema": "app", "name": "users", "type": "table"},
    ]

    assert filter_objects(objects, "card", "card_") == [objects[0]]


def test_default_limit_is_normalized() -> None:
    args = build_parser().parse_args(["objects", "public"])
    validate_args(args)

    assert args.limit == DEFAULT_LIMIT


def test_top_default_limit_is_normalized_to_query_limit() -> None:
    args = build_parser().parse_args(["top"])
    validate_args(args)

    assert args.limit == 10


def test_limit_works_before_top_subcommand() -> None:
    args = build_parser().parse_args(["--limit", "159", "top"])
    validate_args(args)

    assert args.command == "top"
    assert args.limit == 159


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
