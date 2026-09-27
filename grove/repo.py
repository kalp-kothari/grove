"""Repository operations, commit management, LCA resolution, and branch merges."""

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Tuple

from grove.canonical import detect_format, is_config_file, parse_file, serialize
from grove.diff import DiffOp, diff_data
from grove.merge import Conflict, merge_3way
from grove.objectstore import ObjectStore


@dataclass
class MergeBranchResult:
    """Result of a merge_branch operation."""
    commit_hash: Optional[str] = None
    conflicts: List[Conflict] = field(default_factory=list)
    status: str = "merged"  # "merged", "fast-forward", "already-up-to-date", "conflict"

    @property
    def has_conflicts(self) -> bool:
        return len(self.conflicts) > 0

    def __bool__(self) -> bool:
        return not self.has_conflicts

    def __iter__(self) -> Iterator[Any]:
        # Allows tuple unpacking: commit_hash, conflicts = repo.merge_branch(...)
        return iter((self.commit_hash, self.conflicts))

    def __getitem__(self, index: int) -> Any:
        return (self.commit_hash, self.conflicts)[index]

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, str):
            return self.commit_hash == other
        if isinstance(other, list):
            return self.conflicts == other
        return super().__eq__(other)


class Repo:
    """High-level repository manager for Grove."""

    def __init__(self, root_dir: str):
        self.root_dir = os.path.abspath(root_dir)
        self.store = ObjectStore(self.root_dir)

    @classmethod
    def init(cls, path: str = ".") -> "Repo":
        """Initialize a new Grove repository in the specified directory."""
        repo = cls(path)
        repo.store.init_store()
        return repo

    @classmethod
    def find_repo(cls, start_path: str = ".") -> "Repo":
        """Search upwards for .grove repository root."""
        current = os.path.abspath(start_path)
        while True:
            if os.path.exists(os.path.join(current, ".grove")):
                return cls(current)
            parent = os.path.dirname(current)
            if parent == current:
                raise FileNotFoundError("Not a Grove repository (no .grove found in parent directories)")
            current = parent

    def find_config_files(self) -> List[str]:
        """Find all supported config files in the working tree, skipping ignored directories."""
        ignore_dirs = {".grove", ".git", "__pycache__", "venv", ".venv", ".pytest_cache"}
        config_files: List[str] = []

        for root, dirs, files in os.walk(self.root_dir):
            dirs[:] = [d for d in dirs if d not in ignore_dirs]
            for file in files:
                if is_config_file(file):
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, self.root_dir).replace("\\", "/")
                    config_files.append(rel_path)

        return sorted(config_files)

    def write_working_file(self, rel_path: str, data: Any) -> None:
        """Serialize data and write it to the working tree."""
        full_path = os.path.join(self.root_dir, rel_path)
        if os.path.exists(full_path) and os.path.isdir(full_path):
            raise IsADirectoryError(f"Cannot write working file: '{rel_path}' is a directory")
        fmt = detect_format(rel_path)
        content = serialize(data, fmt)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)

    def resolve_commit(self, target: str) -> str:
        """Resolve a branch name or commit hash to a valid commit hash."""
        if not target or not target.strip():
            raise ValueError("Commit or branch identifier cannot be empty")
        if target in self.store.list_branches():
            commit_hash = self.store.get_branch_commit(target)
            if not commit_hash:
                raise RuntimeError(f"Branch '{target}' has no commits")
            return commit_hash
        if self.store.commit_exists(target):
            return target
        raise KeyError(f"Unknown branch or commit: '{target}'")

    def commit(self, files: Optional[List[str]] = None, message: str = "Commit") -> str:
        """Create a new commit snapshotting the working tree config files."""
        if not self.store.is_initialized():
            raise RuntimeError("Repository is not initialized")

        if files is not None and len(files) == 0:
            raise ValueError("No files specified for commit")

        head_commit_hash = self.store.get_head_commit()
        tree: Dict[str, str] = {}

        if head_commit_hash:
            head_commit = self.store.read_commit(head_commit_hash)
            tree.update(head_commit.get("tree", {}))

        targets = files if files is not None else self.find_config_files()
        if not tree and not targets:
            raise RuntimeError("No configuration files found to commit")

        for rel_path in targets:
            full_path = os.path.join(self.root_dir, rel_path)
            if os.path.exists(full_path):
                if os.path.isdir(full_path):
                    raise IsADirectoryError(f"Cannot commit '{rel_path}': path is a directory")
                data = parse_file(full_path)
                blob_hash = self.store.write_blob(data)
                tree[rel_path.replace("\\", "/")] = blob_hash
            elif rel_path in tree:
                # File deleted
                del tree[rel_path]
            else:
                raise FileNotFoundError(f"Cannot commit '{rel_path}': file does not exist and is not currently tracked")

        parents = [head_commit_hash] if head_commit_hash else []
        commit_hash = self.store.write_commit(tree=tree, parents=parents, message=message)

        target, is_branch = self.store.get_head()
        if is_branch:
            self.store.set_branch_commit(target, commit_hash)
        else:
            self.store.set_head(commit_hash, is_branch=False)

        return commit_hash

    def log(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Return commit history starting from HEAD."""
        if limit is not None and limit <= 0:
            raise ValueError(f"Limit must be a positive integer, got {limit}")

        head_commit_hash = self.store.get_head_commit()
        if not head_commit_hash:
            return []

        history: List[Dict[str, Any]] = []
        visited = set()
        queue = [head_commit_hash]

        while queue:
            commit_hash = queue.pop(0)
            if commit_hash in visited:
                continue
            visited.add(commit_hash)

            commit_data = self.store.read_commit(commit_hash)
            commit_entry = dict(commit_data)
            commit_entry["hash"] = commit_hash
            history.append(commit_entry)

            if limit is not None and len(history) >= limit:
                break

            for parent in commit_data.get("parents", []):
                if parent not in visited:
                    queue.append(parent)

        return history

    def find_lca(self, commit_a: str, commit_b: str) -> Optional[str]:
        """Find Lowest Common Ancestor using ancestor-set walk and BFS."""
        if not self.store.commit_exists(commit_a):
            raise KeyError(f"Commit not found: {commit_a}")
        if not self.store.commit_exists(commit_b):
            raise KeyError(f"Commit not found: {commit_b}")

        if commit_a == commit_b:
            return commit_a

        # Collect all ancestors of commit_a
        ancestors_a = set()
        queue = [commit_a]
        while queue:
            curr = queue.pop(0)
            if curr in ancestors_a:
                continue
            ancestors_a.add(curr)
            if self.store.commit_exists(curr):
                c = self.store.read_commit(curr)
                queue.extend(c.get("parents", []))

        # BFS from commit_b to find closest ancestor in ancestors_a
        visited_b = set()
        queue_b = [commit_b]
        while queue_b:
            curr = queue_b.pop(0)
            if curr in ancestors_a:
                return curr
            if curr in visited_b:
                continue
            visited_b.add(curr)
            if self.store.commit_exists(curr):
                c = self.store.read_commit(curr)
                queue_b.extend(c.get("parents", []))

        return None

    def diff_commits(self, commit_a: str, commit_b: str) -> Dict[str, List[DiffOp]]:
        """Compute semantic diffs across all files between two commits."""
        hash_a = self.resolve_commit(commit_a)
        hash_b = self.resolve_commit(commit_b)
        tree_a = self.store.read_commit(hash_a).get("tree", {})
        tree_b = self.store.read_commit(hash_b).get("tree", {})

        all_files = sorted(set(tree_a.keys()) | set(tree_b.keys()))
        results: Dict[str, List[DiffOp]] = {}

        for rel_path in all_files:
            data_a = self.store.read_blob_data(tree_a[rel_path]) if rel_path in tree_a else {}
            data_b = self.store.read_blob_data(tree_b[rel_path]) if rel_path in tree_b else {}
            diffs = diff_data(data_a, data_b)
            if diffs:
                results[rel_path] = diffs

        return results

    def diff_working(self, filepath: Optional[str] = None) -> Dict[str, List[DiffOp]]:
        """Compute semantic diff between the working tree and the current HEAD commit."""
        head_commit_hash = self.store.get_head_commit()
        head_tree = {}
        if head_commit_hash:
            head_tree = self.store.read_commit(head_commit_hash).get("tree", {})

        if filepath:
            rel_path = os.path.relpath(filepath, self.root_dir).replace("\\", "/")
            full_path = os.path.join(self.root_dir, rel_path)
            if not os.path.exists(full_path) and rel_path not in head_tree:
                raise FileNotFoundError(f"File '{filepath}' does not exist and is not tracked")
            if os.path.exists(full_path) and os.path.isdir(full_path):
                raise IsADirectoryError(f"Expected a file, but '{filepath}' is a directory")
            candidates = [rel_path]
        else:
            candidates = sorted(set(head_tree.keys()) | set(self.find_config_files()))

        results: Dict[str, List[DiffOp]] = {}
        for rel_path in candidates:
            full_path = os.path.join(self.root_dir, rel_path)
            data_working = parse_file(full_path) if os.path.exists(full_path) else {}
            data_head = self.store.read_blob_data(head_tree[rel_path]) if rel_path in head_tree else {}

            diffs = diff_data(data_head, data_working)
            if diffs:
                results[rel_path] = diffs

        return results

    def branch(self, name: Optional[str] = None) -> Any:
        """Create a new branch pointing to HEAD, or list all existing branches."""
        if name is None:
            branches = self.store.list_branches()
            current, _ = self.store.get_head()
            return branches, current

        if not name or not name.strip():
            raise ValueError("Branch name cannot be empty")

        head_commit = self.store.get_head_commit()
        if not head_commit:
            raise RuntimeError("Cannot create branch on empty repository without commits")

        if name in self.store.list_branches():
            raise ValueError(f"Branch '{name}' already exists")

        self.store.set_branch_commit(name, head_commit)
        return name

    def checkout(self, target: str) -> None:
        """Checkout a branch or commit and update the working tree."""
        if not target or not target.strip():
            raise ValueError("Checkout target cannot be empty")

        diffs = self.diff_working()
        if diffs:
            uncommitted = ", ".join(diffs.keys())
            raise RuntimeError(
                f"Cannot checkout '{target}': uncommitted changes in working tree ({uncommitted}). "
                "Please commit your changes before switching branches."
            )

        current_head = self.store.get_head_commit()

        branches = self.store.list_branches()
        if target in branches:
            commit_hash = self.store.get_branch_commit(target)
            if not commit_hash:
                raise RuntimeError(f"Branch '{target}' has no commits")
            self.store.set_head(target, is_branch=True)
        elif self.store.commit_exists(target):
            commit_hash = target
            self.store.set_head(target, is_branch=False)
        else:
            raise ValueError(f"Unknown branch or commit: '{target}'")

        # Update working tree files to match checkout commit
        if current_head and current_head != commit_hash:
            current_tree = self.store.read_commit(current_head).get("tree", {})
            target_tree = self.store.read_commit(commit_hash).get("tree", {})
            for rel_path in current_tree:
                if rel_path not in target_tree:
                    full_path = os.path.join(self.root_dir, rel_path)
                    if os.path.exists(full_path) and os.path.isfile(full_path):
                        os.remove(full_path)

        commit_data = self.store.read_commit(commit_hash)
        for rel_path, blob_hash in commit_data.get("tree", {}).items():
            data = self.store.read_blob_data(blob_hash)
            self.write_working_file(rel_path, data)

    def merge_branch(self, other_branch_name: str) -> MergeBranchResult:
        """Merge another branch into the current HEAD branch.

        Includes fast-forward and no-op checks before performing a three-way merge:
        - If lca == ours_hash: pure fast-forward.
        - If lca == theirs_hash: current branch already contains everything (no-op).
        - Otherwise: three-way merge with ancestor LCA.
        """
        if not other_branch_name or not other_branch_name.strip():
            raise ValueError("Branch name cannot be empty")

        if other_branch_name not in self.store.list_branches():
            raise ValueError(f"Branch '{other_branch_name}' not found")

        theirs_hash = self.store.get_branch_commit(other_branch_name)
        if not theirs_hash:
            raise RuntimeError(f"Branch '{other_branch_name}' has no commits")

        ours_hash = self.store.get_head_commit()
        if not ours_hash:
            raise RuntimeError("Current branch has no commits")

        diffs = self.diff_working()
        if diffs:
            uncommitted = ", ".join(diffs.keys())
            raise RuntimeError(
                f"Cannot merge '{other_branch_name}': uncommitted changes in working tree ({uncommitted}). "
                "Please commit your changes before merging."
            )

        target_branch, is_branch = self.store.get_head()

        # Step 1: Find LCA
        lca = self.find_lca(ours_hash, theirs_hash)
        if not lca:
            raise RuntimeError(f"No common ancestor found between HEAD and '{other_branch_name}'")

        # Step 2: Check for no-op (already up to date)
        if lca == theirs_hash:
            return MergeBranchResult(
                commit_hash=ours_hash,
                conflicts=[],
                status="already-up-to-date",
            )

        # Step 3: Check for fast-forward
        if lca == ours_hash:
            # Advance branch pointer
            if is_branch:
                self.store.set_branch_commit(target_branch, theirs_hash)
            else:
                self.store.set_head(theirs_hash, is_branch=False)

            # Clean up files in ours that are not in theirs
            ours_commit = self.store.read_commit(ours_hash)
            ours_tree = ours_commit.get("tree", {})
            theirs_commit = self.store.read_commit(theirs_hash)
            theirs_tree = theirs_commit.get("tree", {})

            for rel_path in ours_tree:
                if rel_path not in theirs_tree:
                    full_path = os.path.join(self.root_dir, rel_path)
                    if os.path.exists(full_path) and os.path.isfile(full_path):
                        os.remove(full_path)

            # Write out files from theirs
            for rel_path, blob_hash in theirs_tree.items():
                data = self.store.read_blob_data(blob_hash)
                self.write_working_file(rel_path, data)

            return MergeBranchResult(
                commit_hash=theirs_hash,
                conflicts=[],
                status="fast-forward",
            )

        # Step 4: Three-way merge
        base_commit = self.store.read_commit(lca)
        ours_commit = self.store.read_commit(ours_hash)
        theirs_commit = self.store.read_commit(theirs_hash)

        tree_base = base_commit.get("tree", {})
        tree_ours = ours_commit.get("tree", {})
        tree_theirs = theirs_commit.get("tree", {})

        all_files = sorted(set(tree_base.keys()) | set(tree_ours.keys()) | set(tree_theirs.keys()))
        all_conflicts: List[Conflict] = []
        merged_files_data: Dict[str, Any] = {}

        for rel_path in all_files:
            in_base = rel_path in tree_base
            in_ours = rel_path in tree_ours
            in_theirs = rel_path in tree_theirs

            if not in_ours and not in_theirs:
                continue

            # Deleted in ours, present in theirs
            if not in_ours and in_theirs:
                if in_base and tree_theirs[rel_path] == tree_base[rel_path]:
                    continue  # accepted deletion
                else:
                    all_conflicts.append(Conflict(rel_path, tree_base.get(rel_path), None, tree_theirs[rel_path]))
                    continue

            # Present in ours, deleted in theirs
            if in_ours and not in_theirs:
                if in_base and tree_ours[rel_path] == tree_base[rel_path]:
                    continue  # accepted deletion
                else:
                    all_conflicts.append(Conflict(rel_path, tree_base.get(rel_path), tree_ours[rel_path], None))
                    continue

            # Present in both ours and theirs
            data_base = self.store.read_blob_data(tree_base[rel_path]) if in_base else {}
            data_ours = self.store.read_blob_data(tree_ours[rel_path])
            data_theirs = self.store.read_blob_data(tree_theirs[rel_path])

            res = merge_3way(data_base, data_ours, data_theirs)
            if res.has_conflicts:
                all_conflicts.extend(res.conflicts)
            merged_files_data[rel_path] = res.data

        if all_conflicts:
            # Return conflict result without creating commit or touching working directory
            return MergeBranchResult(
                commit_hash=None,
                conflicts=all_conflicts,
                status="conflict",
            )

        # Remove deleted files from disk
        for rel_path in tree_ours:
            if rel_path not in merged_files_data:
                full_path = os.path.join(self.root_dir, rel_path)
                if os.path.exists(full_path) and os.path.isfile(full_path):
                    os.remove(full_path)

        # Apply merged files and create merge commit
        merged_tree: Dict[str, str] = {}
        for rel_path, data in merged_files_data.items():
            self.write_working_file(rel_path, data)
            blob_hash = self.store.write_blob(data)
            merged_tree[rel_path] = blob_hash

        merge_msg = f"Merge branch '{other_branch_name}' into {target_branch}"
        merge_commit_hash = self.store.write_commit(
            tree=merged_tree,
            parents=[ours_hash, theirs_hash],
            message=merge_msg,
        )

        if is_branch:
            self.store.set_branch_commit(target_branch, merge_commit_hash)
        else:
            self.store.set_head(merge_commit_hash, is_branch=False)

        return MergeBranchResult(
            commit_hash=merge_commit_hash,
            conflicts=[],
            status="merged",
        )
