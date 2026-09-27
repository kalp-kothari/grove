"""Semantic tree diff engine for structured configuration files."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


IDENTITY_KEYS = ("id", "name", "key", "_id")


@dataclass
class DiffOp:
    """Represents a single semantic difference."""
    op: str  # 'ADD', 'REMOVE', 'CHANGE'
    path: str
    old_value: Any
    new_value: Any

    def __str__(self) -> str:
        if self.op == "ADD":
            return f"+ {self.path}: {self.new_value!r}"
        if self.op == "REMOVE":
            return f"- {self.path}: {self.old_value!r}"
        return f"~ {self.path}: {self.old_value!r} -> {self.new_value!r}"


def find_identity_key(list_a: List[Any], list_b: List[Any]) -> Optional[str]:
    """Find a common identity field (id, name, key, _id) shared by all dict items."""
    all_items = [item for item in (list_a + list_b) if isinstance(item, dict)]
    if not all_items:
        return None

    # All items in both lists must be dicts
    if len(all_items) != (len(list_a) + len(list_b)):
        return None

    for candidate in IDENTITY_KEYS:
        # Candidate must exist in every dict
        if not all(candidate in item for item in all_items):
            continue

        # Candidate values must be hashable and unique within each list
        try:
            keys_a = [item[candidate] for item in list_a]
            keys_b = [item[candidate] for item in list_b]
            if len(keys_a) == len(set(keys_a)) and len(keys_b) == len(set(keys_b)):
                return candidate
        except TypeError:
            continue

    return None


def _format_path(parent_path: str, key: str) -> str:
    if not parent_path:
        return str(key)
    return f"{parent_path}.{key}"


def diff_data(a: Any, b: Any, path: str = "") -> List[DiffOp]:
    """Compute semantic difference between two structured datasets.

    - Independent of dict key order.
    - Matches list items by identity key ('id', 'name', 'key', '_id') if present.
    - Falls back to positional index diff for scalar or anonymous lists.
    """
    diffs: List[DiffOp] = []

    # If types differ, report complete change
    if type(a) is not type(b):
        diffs.append(DiffOp("CHANGE", path or "root", a, b))
        return diffs

    # Dictionaries: compare keys independently of order
    if isinstance(a, dict):
        keys_a = set(a.keys())
        keys_b = set(b.keys())

        # Keys only in a (removed)
        for k in sorted(keys_a - keys_b, key=str):
            diffs.append(DiffOp("REMOVE", _format_path(path, str(k)), a[k], None))

        # Keys only in b (added)
        for k in sorted(keys_b - keys_a, key=str):
            diffs.append(DiffOp("ADD", _format_path(path, str(k)), None, b[k]))

        # Common keys (check recursive diff)
        for k in sorted(keys_a & keys_b, key=str):
            child_path = _format_path(path, str(k))
            diffs.extend(diff_data(a[k], b[k], child_path))

        return diffs

    # Lists: identity-based or positional
    if isinstance(a, list):
        id_key = find_identity_key(a, b)
        if id_key:
            map_a = {item[id_key]: item for item in a}
            map_b = {item[id_key]: item for item in b}

            ids_a = set(map_a.keys())
            ids_b = set(map_b.keys())

            # Removed items
            for item_id in sorted(ids_a - ids_b, key=str):
                item_path = f"{path}[{id_key}={item_id}]" if path else f"[{id_key}={item_id}]"
                diffs.append(DiffOp("REMOVE", item_path, map_a[item_id], None))

            # Added items
            for item_id in sorted(ids_b - ids_a, key=str):
                item_path = f"{path}[{id_key}={item_id}]" if path else f"[{id_key}={item_id}]"
                diffs.append(DiffOp("ADD", item_path, None, map_b[item_id]))

            # Common items: recurse into fields
            for item_id in sorted(ids_a & ids_b, key=str):
                item_path = f"{path}[{id_key}={item_id}]" if path else f"[{id_key}={item_id}]"
                diffs.extend(diff_data(map_a[item_id], map_b[item_id], item_path))

            return diffs

        # Positional fallback for scalar or non-identifiable lists
        common_len = min(len(a), len(b))
        for i in range(common_len):
            idx_path = f"{path}[{i}]" if path else f"[{i}]"
            diffs.extend(diff_data(a[i], b[i], idx_path))

        if len(b) > len(a):
            for i in range(len(a), len(b)):
                idx_path = f"{path}[{i}]" if path else f"[{i}]"
                diffs.append(DiffOp("ADD", idx_path, None, b[i]))
        elif len(a) > len(b):
            for i in range(len(b), len(a)):
                idx_path = f"{path}[{i}]" if path else f"[{i}]"
                diffs.append(DiffOp("REMOVE", idx_path, a[i], None))

        return diffs

    # Scalars (str, int, float, bool, None)
    if a != b:
        diffs.append(DiffOp("CHANGE", path or "root", a, b))

    return diffs


def format_diff(diffs: List[DiffOp]) -> str:
    """Format a list of DiffOp objects into a clean, human-readable string."""
    if not diffs:
        return "(no semantic differences)"
    return "\n".join(str(op) for op in diffs)
