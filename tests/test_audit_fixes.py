"""Tests covering all silent-failure and error-handling audit fixes in Grove."""

import os
import pytest
import yaml

from grove.canonical import parse_file, parse_str
from grove.cli import main
from grove.objectstore import ObjectStore
from grove.repo import MergeBranchResult, Repo


def test_checkout_with_uncommitted_changes_raises(tmp_path):
    """checkout() must not silently overwrite uncommitted working-directory edits."""
    repo = Repo.init(str(tmp_path))
    cfg = tmp_path / "config.yaml"
    cfg.write_text("server:\n  port: 8080\n", encoding="utf-8")
    repo.commit(message="Initial commit")

    repo.branch("feature")

    # Modify working file without committing
    cfg.write_text("server:\n  port: 9999\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="uncommitted changes"):
        repo.checkout("feature")

    # Verify working tree content was NOT overwritten
    data = parse_file(str(cfg))
    assert data["server"]["port"] == 9999


def test_merge_with_uncommitted_changes_raises(tmp_path):
    """merge_branch() must not silently overwrite uncommitted working-directory edits."""
    repo = Repo.init(str(tmp_path))
    cfg = tmp_path / "config.yaml"
    cfg.write_text("server:\n  port: 8080\n", encoding="utf-8")
    repo.commit(message="Initial commit")

    repo.branch("feature")
    repo.checkout("feature")
    cfg.write_text("server:\n  port: 9090\n", encoding="utf-8")
    repo.commit(message="Feature commit")

    repo.checkout("main")
    # Make uncommitted edit on main
    cfg.write_text("server:\n  port: 7070\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="uncommitted changes"):
        repo.merge_branch("feature")

    # Verify uncommitted edit is preserved
    data = parse_file(str(cfg))
    assert data["server"]["port"] == 7070


def test_diff_working_nonexistent_file_raises(tmp_path):
    """diff_working() on a nonexistent and untracked file must raise FileNotFoundError, not report false success."""
    repo = Repo.init(str(tmp_path))
    cfg = tmp_path / "config.yaml"
    cfg.write_text("a: 1\n", encoding="utf-8")
    repo.commit(message="Initial commit")

    with pytest.raises(FileNotFoundError, match="does not exist and is not tracked"):
        repo.diff_working("ghost.yaml")


def test_parse_file_directory_raises_is_a_directory_error(tmp_path):
    """parse_file() on a directory path must raise IsADirectoryError, not a cryptic PermissionError."""
    dir_path = tmp_path / "some_dir.yaml"
    dir_path.mkdir()

    with pytest.raises(IsADirectoryError, match="Cannot parse directory"):
        parse_file(str(dir_path))


def test_parse_file_malformed_syntax_raises_clear_error(tmp_path):
    """parse_file() on corrupt YAML/JSON must raise ValueError with the file name and format."""
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("key: [unclosed list", encoding="utf-8")

    with pytest.raises(ValueError, match="Failed to parse configuration file.*bad.yaml"):
        parse_file(str(bad_yaml))


def test_parse_str_json_null_returns_empty_dict():
    """parse_str() with JSON 'null' must return empty dict {}, matching YAML behavior."""
    assert parse_str("null", "json") == {}
    assert parse_str("null", "yaml") == {}
    assert parse_str("", "json") == {}
    assert parse_str("", "yaml") == {}


def test_branch_already_exists_raises(tmp_path):
    """repo.branch(name) must raise ValueError if the branch name already exists, not overwrite it silently."""
    repo = Repo.init(str(tmp_path))
    cfg = tmp_path / "config.yaml"
    cfg.write_text("a: 1\n", encoding="utf-8")
    repo.commit(message="Initial")

    repo.branch("dev")
    with pytest.raises(ValueError, match="already exists"):
        repo.branch("dev")


def test_find_lca_nonexistent_commit_raises(tmp_path):
    """find_lca() must raise KeyError when passed nonexistent commit hashes."""
    repo = Repo.init(str(tmp_path))
    cfg = tmp_path / "config.yaml"
    cfg.write_text("a: 1\n", encoding="utf-8")
    c1 = repo.commit(message="Initial")

    with pytest.raises(KeyError, match="Commit not found: nonexistent"):
        repo.find_lca("nonexistent", "nonexistent")

    with pytest.raises(KeyError, match="Commit not found: nonexistent"):
        repo.find_lca(c1, "nonexistent")


def test_merge_branch_with_no_commits_raises(tmp_path):
    """merge_branch() must report 'has no commits' when branch exists but has no commit ref."""
    repo = Repo.init(str(tmp_path))
    cfg = tmp_path / "config.yaml"
    cfg.write_text("a: 1\n", encoding="utf-8")
    repo.commit(message="Initial")

    # Manually create an empty branch ref
    empty_branch_file = tmp_path / ".grove" / "refs" / "heads" / "empty_branch"
    empty_branch_file.write_text("", encoding="utf-8")

    with pytest.raises(RuntimeError, match="has no commits"):
        repo.merge_branch("empty_branch")


def test_commit_empty_files_or_no_configs_raises(tmp_path):
    """repo.commit() on an empty repository with no configs or with empty files list must fail loudly."""
    repo = Repo.init(str(tmp_path))

    # Committing on a repo with no config files
    with pytest.raises(RuntimeError, match="No configuration files found to commit"):
        repo.commit()

    # Committing with empty list
    with pytest.raises(ValueError, match="No files specified for commit"):
        repo.commit(files=[])


def test_objectstore_empty_hash_checks(tmp_path):
    """ObjectStore must reject empty strings and not confuse directory paths with objects."""
    store = ObjectStore(str(tmp_path))
    store.init_store()

    assert not store.commit_exists("")
    with pytest.raises(KeyError, match="Invalid commit hash"):
        store.read_commit("")

    with pytest.raises(KeyError, match="Invalid blob hash"):
        store.read_blob("")


def test_diff_commits_resolves_branch_names(tmp_path):
    """diff_commits() must accept branch names in addition to commit hashes."""
    repo = Repo.init(str(tmp_path))
    cfg = tmp_path / "config.yaml"
    cfg.write_text("a: 1\n", encoding="utf-8")
    repo.commit(message="Initial")

    repo.branch("dev")
    repo.checkout("dev")
    cfg.write_text("a: 2\n", encoding="utf-8")
    repo.commit(message="Update a to 2")

    # Diff using branch names
    diffs = repo.diff_commits("main", "dev")
    assert "config.yaml" in diffs
    assert len(diffs["config.yaml"]) == 1
    assert diffs["config.yaml"][0].op == "CHANGE"


def test_checkout_removes_deleted_files(tmp_path):
    """checkout() must remove files from working directory that do not exist in the target commit."""
    repo = Repo.init(str(tmp_path))
    cfg1 = tmp_path / "config.yaml"
    cfg1.write_text("a: 1\n", encoding="utf-8")
    repo.commit(message="Initial on main")

    repo.branch("feature")
    repo.checkout("feature")

    # Add a second file on feature
    cfg2 = tmp_path / "extra.yaml"
    cfg2.write_text("b: 2\n", encoding="utf-8")
    repo.commit(message="Add extra.yaml on feature")

    assert os.path.exists(str(cfg2))

    # Checkout main - extra.yaml should be removed from working tree
    repo.checkout("main")
    assert not os.path.exists(str(cfg2))
    assert os.path.exists(str(cfg1))


def test_cli_merge_missing_else_branch(monkeypatch):
    """CLI merge command must raise error on unexpected status from merge_branch."""
    class DummyResult(MergeBranchResult):
        pass

    dummy = DummyResult(commit_hash="abc1234", conflicts=[], status="unknown_status")

    monkeypatch.setattr(Repo, "find_repo", lambda _: None)
    monkeypatch.setattr(Repo, "merge_branch", lambda self, branch: dummy)

    code = main(["merge", "test_branch"])
    assert code == 1
