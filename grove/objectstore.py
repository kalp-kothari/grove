"""Object store implementing a content-addressed storage model for Grove."""

import os
import json
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from grove.canonical import canonical_bytes, hash_data


class ObjectStore:
    """Manages the content-addressed objects, commits, and references in .grove."""

    def __init__(self, root_dir: str):
        self.root_dir = os.path.abspath(root_dir)
        self.grove_dir = os.path.join(self.root_dir, ".grove")
        self.objects_dir = os.path.join(self.grove_dir, "objects")
        self.commits_dir = os.path.join(self.grove_dir, "commits")
        self.refs_dir = os.path.join(self.grove_dir, "refs", "heads")
        self.head_file = os.path.join(self.grove_dir, "HEAD")

    def init_store(self, default_branch: str = "main") -> None:
        """Initialize the .grove directory structure."""
        os.makedirs(self.objects_dir, exist_ok=True)
        os.makedirs(self.commits_dir, exist_ok=True)
        os.makedirs(self.refs_dir, exist_ok=True)

        if not os.path.exists(self.head_file):
            self.set_head(default_branch, is_branch=True)

    def is_initialized(self) -> bool:
        """Check if repository is initialized."""
        return os.path.exists(self.grove_dir) and os.path.exists(self.head_file)

    def write_blob(self, data: Any) -> str:
        """Store semantic data as a content-addressed blob.

        Uses deterministic canonical bytes so identical semantic content
        always yields the exact same hash and blob.
        """
        raw = canonical_bytes(data)
        blob_hash = hashlib.sha1(raw).hexdigest()
        path = os.path.join(self.objects_dir, blob_hash)
        if not os.path.exists(path):
            with open(path, "wb") as f:
                f.write(raw)
        return blob_hash

    def read_blob(self, blob_hash: str) -> bytes:
        """Read raw bytes of a blob by hash."""
        if not blob_hash or not isinstance(blob_hash, str):
            raise KeyError(f"Invalid blob hash: {blob_hash!r}")
        path = os.path.join(self.objects_dir, blob_hash)
        if not os.path.isfile(path):
            raise KeyError(f"Blob object not found: {blob_hash}")
        with open(path, "rb") as f:
            return f.read()

    def read_blob_data(self, blob_hash: str) -> Any:
        """Read and parse semantic data of a blob."""
        raw = self.read_blob(blob_hash)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"Corrupted blob object '{blob_hash}': {e}") from e

    def write_commit(
        self,
        tree: Dict[str, str],
        parents: List[str],
        message: str,
        author: str = "Grove User",
        timestamp: Optional[str] = None,
    ) -> str:
        """Create and store a commit object."""
        if timestamp is None:
            timestamp = datetime.now(timezone.utc).isoformat()

        commit_data = {
            "tree": tree,
            "parents": parents,
            "message": message,
            "author": author,
            "timestamp": timestamp,
        }

        # Deterministic commit hash
        commit_bytes = json.dumps(commit_data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        commit_hash = hashlib.sha1(commit_bytes).hexdigest()
        path = os.path.join(self.commits_dir, commit_hash)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(commit_data, f, indent=2)

        return commit_hash

    def read_commit(self, commit_hash: str) -> Dict[str, Any]:
        """Read a commit object by hash."""
        if not commit_hash or not isinstance(commit_hash, str):
            raise KeyError(f"Invalid commit hash: {commit_hash!r}")
        path = os.path.join(self.commits_dir, commit_hash)
        if not os.path.isfile(path):
            raise KeyError(f"Commit not found: {commit_hash}")
        with open(path, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError as e:
                raise ValueError(f"Corrupted commit object '{commit_hash}': {e}") from e

    def commit_exists(self, commit_hash: str) -> bool:
        """Check if commit hash exists."""
        if not commit_hash or not isinstance(commit_hash, str):
            return False
        return os.path.isfile(os.path.join(self.commits_dir, commit_hash))

    def get_head(self) -> Tuple[str, bool]:
        """Return (target, is_branch).

        If is_branch is True, target is the branch name (e.g., 'main').
        If is_branch is False, target is a detached commit hash.
        """
        if not os.path.isfile(self.head_file):
            return "main", True
        with open(self.head_file, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            return "main", True
        if content.startswith("ref: refs/heads/"):
            return content.split("ref: refs/heads/")[1], True
        return content, False

    def set_head(self, target: str, is_branch: bool = True) -> None:
        """Update HEAD reference."""
        if not target or not target.strip():
            raise ValueError("HEAD target cannot be empty")
        with open(self.head_file, "w", encoding="utf-8") as f:
            if is_branch:
                f.write(f"ref: refs/heads/{target}\n")
            else:
                f.write(f"{target}\n")

    def get_branch_commit(self, branch_name: str) -> Optional[str]:
        """Get the commit hash of a branch tip."""
        if not branch_name or not isinstance(branch_name, str):
            return None
        path = os.path.join(self.refs_dir, branch_name)
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip() or None

    def set_branch_commit(self, branch_name: str, commit_hash: str) -> None:
        """Set the commit hash of a branch tip."""
        if not branch_name or not branch_name.strip():
            raise ValueError("Branch name cannot be empty")
        if not commit_hash or not commit_hash.strip():
            raise ValueError("Commit hash cannot be empty")
        path = os.path.join(self.refs_dir, branch_name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"{commit_hash}\n")

    def list_branches(self) -> List[str]:
        """List all branch names."""
        if not os.path.isdir(self.refs_dir):
            return []
        return sorted([f for f in os.listdir(self.refs_dir) if os.path.isfile(os.path.join(self.refs_dir, f))])

    def get_head_commit(self) -> Optional[str]:
        """Resolve HEAD to current commit hash."""
        target, is_branch = self.get_head()
        if is_branch:
            return self.get_branch_commit(target)
        return target if self.commit_exists(target) else None
