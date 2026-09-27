"""End-to-end repository tests mirroring the demo scenario and fast-forward merges."""

import os
import pytest
import yaml

from grove.canonical import parse_file
from grove.repo import Repo


def test_demo_scenario_end_to_end(tmp_path):
    """Mirror the exact demo scenario requested for Grove:

    a. init + commit a base YAML config on main
    b. branch feature, edit field A, commit
    c. back on main, edit unrelated field B, commit — assert merge_branch('feature')
       auto-merges with zero conflicts and both edits present
    d. new scenario: both branches edit the same field differently — assert merge_branch
       returns a non-empty conflict list, and does not silently pick a winner
    e. reorder keys / reformat whitespace in the working file with no real value change —
       assert diff_working returns an empty change list
    """
    repo = Repo.init(str(tmp_path))
    config_file = tmp_path / "config.yaml"

    # Step a: init + commit base YAML config on main
    base_config = {
        "server": {
            "host": "0.0.0.0",
            "port": 8080,
            "debug": False,
        },
        "database": {
            "host": "localhost",
            "port": 5432,
            "max_connections": 20,
        },
    }
    config_file.write_text(yaml.safe_dump(base_config, sort_keys=False), encoding="utf-8")
    commit_base = repo.commit(message="Initial base config")
    assert commit_base is not None

    # Step b: branch feature, edit field A (server.port -> 9090), commit
    repo.branch("feature")
    repo.checkout("feature")

    feature_config = parse_file(str(config_file))
    feature_config["server"]["port"] = 9090
    config_file.write_text(yaml.safe_dump(feature_config, sort_keys=False), encoding="utf-8")
    commit_feat = repo.commit(message="Update server port to 9090 on feature")
    assert commit_feat != commit_base

    # Step c: back on main, edit unrelated field B (database.max_connections -> 50), commit
    repo.checkout("main")
    main_config = parse_file(str(config_file))
    main_config["database"]["max_connections"] = 50
    config_file.write_text(yaml.safe_dump(main_config, sort_keys=False), encoding="utf-8")
    commit_main = repo.commit(message="Increase max_connections to 50 on main")
    assert commit_main != commit_base

    # Assert merge_branch('feature') auto-merges with zero conflicts and both edits present
    merge_result = repo.merge_branch("feature")
    assert not merge_result.has_conflicts
    assert len(merge_result.conflicts) == 0
    assert merge_result.commit_hash is not None

    # Verify both edits are present in working file and merged state
    merged_data = parse_file(str(config_file))
    assert merged_data["server"]["port"] == 9090
    assert merged_data["database"]["max_connections"] == 50
    assert merged_data["server"]["host"] == "0.0.0.0"
    assert merged_data["database"]["port"] == 5432

    # Step d: new scenario: both branches edit the same field differently
    # Create branches 'branch_conflict_a' and 'branch_conflict_b' from current merged main
    repo.branch("conflict_a")
    repo.checkout("conflict_a")
    conf_a_data = parse_file(str(config_file))
    conf_a_data["server"]["port"] = 9100
    config_file.write_text(yaml.safe_dump(conf_a_data, sort_keys=False), encoding="utf-8")
    repo.commit(message="Change server.port to 9100 on conflict_a")

    repo.checkout("main")
    repo.branch("conflict_b")
    repo.checkout("conflict_b")
    conf_b_data = parse_file(str(config_file))
    conf_b_data["server"]["port"] = 9200
    config_file.write_text(yaml.safe_dump(conf_b_data, sort_keys=False), encoding="utf-8")
    repo.commit(message="Change server.port to 9200 on conflict_b")

    # Now on conflict_b, merge conflict_a
    conflict_result = repo.merge_branch("conflict_a")
    # Assert merge_branch returns a non-empty conflict list, and does not silently pick a winner
    assert conflict_result.has_conflicts
    assert len(conflict_result.conflicts) > 0
    assert conflict_result.commit_hash is None
    paths = [c.path for c in conflict_result.conflicts]
    assert "server.port" in paths

    # Step e: reorder keys / reformat whitespace in the working file with no real value change
    # Assert diff_working returns an empty change list
    repo.checkout("main")
    reordered_yaml = """
# Formatted with comments and rearranged keys
database:
  max_connections: 50
  port: 5432
  host: "localhost"

server:
  debug: false
  port: 9090
  host: "0.0.0.0"
"""
    config_file.write_text(reordered_yaml, encoding="utf-8")
    working_diffs = repo.diff_working()

    # The diffs dictionary should either have no diffs or diff list for config.yaml must be empty
    file_diffs = working_diffs.get("config.yaml", [])
    assert len(file_diffs) == 0


def test_fast_forward_and_noop_merges(tmp_path):
    """Test fast-forward and no-op merge handling in repo.py."""
    repo = Repo.init(str(tmp_path))
    cfg = tmp_path / "app.json"
    cfg.write_text('{"version": "1.0"}', encoding="utf-8")
    commit_1 = repo.commit(message="v1.0")

    # Branch dev and advance it
    repo.branch("dev")
    repo.checkout("dev")
    cfg.write_text('{"version": "1.1"}', encoding="utf-8")
    commit_2 = repo.commit(message="v1.1")

    # On dev, merging main should be a no-op (main is an ancestor of dev)
    noop_result = repo.merge_branch("main")
    assert noop_result.status == "already-up-to-date"
    assert noop_result.commit_hash == commit_2
    assert not noop_result.has_conflicts

    # On main, merging dev should be a pure fast-forward (main is ancestor of dev)
    repo.checkout("main")
    ff_result = repo.merge_branch("dev")
    assert ff_result.status == "fast-forward"
    assert ff_result.commit_hash == commit_2
    assert not ff_result.has_conflicts

    # Verify working file on main is updated to v1.1
    updated_data = parse_file(str(cfg))
    assert updated_data["version"] == "1.1"
