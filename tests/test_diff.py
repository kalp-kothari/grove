"""Tests for semantic diff engine in Grove."""

import pytest
from grove.canonical import canonical_bytes, hash_data, parse_str
from grove.diff import diff_data, format_diff


def test_dict_key_reordering_produces_no_diff():
    data1 = {"server": {"host": "localhost", "port": 8080}, "env": "prod"}
    data2 = {"env": "prod", "server": {"port": 8080, "host": "localhost"}}

    diffs = diff_data(data1, data2)
    assert len(diffs) == 0
    assert format_diff(diffs) == "(no semantic differences)"


def test_whitespace_and_formatting_no_canonical_difference():
    yaml_a = """
    server:
      host: localhost
      port: 8080
    """
    yaml_b = """
    server:
      port: 8080

      host:   "localhost"
    """
    data_a = parse_str(yaml_a, "yaml")
    data_b = parse_str(yaml_b, "yaml")

    assert canonical_bytes(data_a) == canonical_bytes(data_b)
    assert hash_data(data_a) == hash_data(data_b)
    assert len(diff_data(data_a, data_b)) == 0


def test_nested_field_change():
    base = {"database": {"host": "127.0.0.1", "port": 5432, "pool": 10}}
    target = {"database": {"host": "127.0.0.1", "port": 5433, "pool": 10}}

    diffs = diff_data(base, target)
    assert len(diffs) == 1
    assert diffs[0].op == "CHANGE"
    assert diffs[0].path == "database.port"
    assert diffs[0].old_value == 5432
    assert diffs[0].new_value == 5433


def test_added_and_removed_keys():
    base = {"a": 1, "b": 2}
    target = {"b": 2, "c": 3}

    diffs = diff_data(base, target)
    ops = {d.op: d for d in diffs}
    assert "REMOVE" in ops
    assert ops["REMOVE"].path == "a"
    assert "ADD" in ops
    assert ops["ADD"].path == "c"


def test_list_by_id_reorder_produces_no_diff():
    list_a = [
        {"id": "service-auth", "port": 4001, "enabled": True},
        {"id": "service-billing", "port": 4002, "enabled": False},
    ]
    list_b = [
        {"id": "service-billing", "port": 4002, "enabled": False},
        {"id": "service-auth", "port": 4001, "enabled": True},
    ]

    diffs = diff_data(list_a, list_b)
    assert len(diffs) == 0


def test_list_by_id_nested_property_change():
    list_a = [
        {"name": "web", "replicas": 2},
        {"name": "worker", "replicas": 5},
    ]
    list_b = [
        {"name": "worker", "replicas": 10},
        {"name": "web", "replicas": 2},
    ]

    diffs = diff_data(list_a, list_b)
    assert len(diffs) == 1
    assert diffs[0].op == "CHANGE"
    assert diffs[0].path == "[name=worker].replicas"
    assert diffs[0].old_value == 5
    assert diffs[0].new_value == 10


def test_scalar_list_positional_fallback():
    list_a = ["apple", "banana"]
    list_b = ["apple", "blueberry"]

    diffs = diff_data(list_a, list_b)
    assert len(diffs) == 1
    assert diffs[0].op == "CHANGE"
    assert diffs[0].path == "[1]"
    assert diffs[0].old_value == "banana"
    assert diffs[0].new_value == "blueberry"
