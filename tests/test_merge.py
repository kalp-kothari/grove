"""Tests for three-way field-level merge engine in Grove."""

import pytest
from grove.merge import format_conflicts, merge_3way


def test_disjoint_field_edits_auto_merge():
    base = {
        "server": {"host": "0.0.0.0", "port": 8080},
        "database": {"host": "localhost", "port": 5432},
    }
    # Ours changes server.port
    ours = {
        "server": {"host": "0.0.0.0", "port": 9000},
        "database": {"host": "localhost", "port": 5432},
    }
    # Theirs changes database.host
    theirs = {
        "server": {"host": "0.0.0.0", "port": 8080},
        "database": {"host": "db.production.internal", "port": 5432},
    }

    res = merge_3way(base, ours, theirs)
    assert not res.has_conflicts
    assert len(res.conflicts) == 0
    assert res.data["server"]["port"] == 9000
    assert res.data["database"]["host"] == "db.production.internal"
    assert res.data["server"]["host"] == "0.0.0.0"
    assert res.data["database"]["port"] == 5432


def test_same_field_different_values_conflict():
    base = {"server": {"port": 8080}}
    ours = {"server": {"port": 9000}}
    theirs = {"server": {"port": 9090}}

    res = merge_3way(base, ours, theirs)
    assert res.has_conflicts
    assert len(res.conflicts) == 1
    assert res.conflicts[0].path == "server.port"
    assert res.conflicts[0].base == 8080
    assert res.conflicts[0].ours == 9000
    assert res.conflicts[0].theirs == 9090

    output = format_conflicts(res.conflicts)
    assert "server.port" in output
    assert "9000" in output
    assert "9090" in output


def test_same_field_identical_values_no_conflict():
    base = {"server": {"port": 8080}}
    ours = {"server": {"port": 9000}}
    theirs = {"server": {"port": 9000}}

    res = merge_3way(base, ours, theirs)
    assert not res.has_conflicts
    assert res.data["server"]["port"] == 9000


def test_list_by_id_merge():
    base = [
        {"id": "auth", "port": 3000, "workers": 2},
        {"id": "api", "port": 4000, "workers": 4},
    ]
    # Ours increases auth workers
    ours = [
        {"id": "auth", "port": 3000, "workers": 5},
        {"id": "api", "port": 4000, "workers": 4},
    ]
    # Theirs changes api port and adds cache service
    theirs = [
        {"id": "auth", "port": 3000, "workers": 2},
        {"id": "api", "port": 4001, "workers": 4},
        {"id": "cache", "port": 6379, "workers": 1},
    ]

    res = merge_3way(base, ours, theirs)
    assert not res.has_conflicts
    by_id = {item["id"]: item for item in res.data}
    assert by_id["auth"]["workers"] == 5
    assert by_id["api"]["port"] == 4001
    assert by_id["cache"]["port"] == 6379


def test_list_by_id_conflicting_property():
    base = [{"id": "web", "replicas": 2}]
    ours = [{"id": "web", "replicas": 4}]
    theirs = [{"id": "web", "replicas": 6}]

    res = merge_3way(base, ours, theirs)
    assert res.has_conflicts
    assert len(res.conflicts) == 1
    assert "[id=web].replicas" in res.conflicts[0].path
