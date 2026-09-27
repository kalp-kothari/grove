"""Three-way field-level semantic merge engine."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from grove.diff import find_identity_key


@dataclass
class Conflict:
    """Represents a merge conflict on a specific field path."""
    path: str
    base: Any
    ours: Any
    theirs: Any

    def __str__(self) -> str:
        return (
            f"CONFLICT at '{self.path}':\n"
            f"  base:   {self.base!r}\n"
            f"  ours:   {self.ours!r}\n"
            f"  theirs: {self.theirs!r}"
        )


@dataclass
class MergeResult:
    """Result of a three-way merge containing merged data and any conflicts."""
    data: Any
    conflicts: List[Conflict]

    @property
    def has_conflicts(self) -> bool:
        return len(self.conflicts) > 0


def format_conflicts(conflicts: List[Conflict]) -> str:
    """Format a list of merge conflicts for display."""
    if not conflicts:
        return ""
    lines = [f"Found {len(conflicts)} merge conflict(s):"]
    for c in conflicts:
        lines.append(f"  - Field '{c.path}':")
        lines.append(f"      base:   {c.base!r}")
        lines.append(f"      ours:   {c.ours!r}")
        lines.append(f"      theirs: {c.theirs!r}")
    return "\n".join(lines)


def merge_3way(base: Any, ours: Any, theirs: Any, path: str = "") -> MergeResult:
    """Recursively perform a three-way field-level merge.

    Rules:
    - If ours == theirs: no conflict, return ours.
    - If ours == base: return theirs (only theirs changed).
    - If theirs == base: return ours (only ours changed).
    - If both changed differently:
      - If both ours and theirs are dicts: recurse into individual keys!
      - If both are lists of dicts with an identity key: match and merge items by id.
      - If scalar list or mismatched types/scalars: emit a Conflict.
    """
    # 1. Both identical
    if ours == theirs:
        return MergeResult(ours, [])

    # 2. Only theirs changed
    if ours == base:
        return MergeResult(theirs, [])

    # 3. Only ours changed
    if theirs == base:
        return MergeResult(ours, [])

    # 4. Both changed differently
    # 4A. Dict merge (recursive key-by-key)
    if isinstance(ours, dict) and isinstance(theirs, dict):
        base_dict = base if isinstance(base, dict) else {}
        all_keys = sorted(set(base_dict.keys()) | set(ours.keys()) | set(theirs.keys()), key=str)

        merged_dict: Dict[str, Any] = {}
        conflicts: List[Conflict] = []

        for k in all_keys:
            child_path = f"{path}.{k}" if path else str(k)
            in_base = k in base_dict
            in_ours = k in ours
            in_theirs = k in theirs

            if not in_base:
                # Key added after base
                if in_ours and not in_theirs:
                    # Added only by ours
                    merged_dict[k] = ours[k]
                elif in_theirs and not in_ours:
                    # Added only by theirs
                    merged_dict[k] = theirs[k]
                else:
                    # Added by both
                    if ours[k] == theirs[k]:
                        merged_dict[k] = ours[k]
                    elif isinstance(ours[k], dict) and isinstance(theirs[k], dict):
                        res = merge_3way({}, ours[k], theirs[k], child_path)
                        merged_dict[k] = res.data
                        conflicts.extend(res.conflicts)
                    else:
                        conflicts.append(Conflict(child_path, None, ours[k], theirs[k]))
                        merged_dict[k] = ours[k]
            else:
                # Key present in base
                base_val = base_dict[k]
                if not in_ours and not in_theirs:
                    # Deleted by both
                    continue
                elif not in_ours and in_theirs:
                    # Deleted by ours, present in theirs
                    if theirs[k] == base_val:
                        # Theirs did not change it -> accept deletion
                        continue
                    else:
                        # Theirs modified it, ours deleted it -> conflict
                        conflicts.append(Conflict(child_path, base_val, None, theirs[k]))
                        merged_dict[k] = theirs[k]
                elif in_ours and not in_theirs:
                    # Deleted by theirs, present in ours
                    if ours[k] == base_val:
                        # Ours did not change it -> accept deletion
                        continue
                    else:
                        # Ours modified it, theirs deleted it -> conflict
                        conflicts.append(Conflict(child_path, base_val, ours[k], None))
                        merged_dict[k] = ours[k]
                else:
                    # Present in all three
                    res = merge_3way(base_val, ours[k], theirs[k], child_path)
                    merged_dict[k] = res.data
                    conflicts.extend(res.conflicts)

        return MergeResult(merged_dict, conflicts)

    # 4B. List merge
    if isinstance(ours, list) and isinstance(theirs, list):
        id_key = find_identity_key(ours, theirs)
        if id_key and isinstance(base, list):
            base_map = {item[id_key]: item for item in base if isinstance(item, dict) and id_key in item}
            ours_map = {item[id_key]: item for item in ours if isinstance(item, dict) and id_key in item}
            theirs_map = {item[id_key]: item for item in theirs if isinstance(item, dict) and id_key in item}

            # Order: preserve ours order, then append any new entries introduced in theirs
            id_order = []
            for item in ours:
                if item[id_key] not in id_order:
                    id_order.append(item[id_key])
            for item in theirs:
                if item[id_key] not in id_order:
                    id_order.append(item[id_key])

            merged_list: List[Any] = []
            conflicts: List[Conflict] = []

            for item_id in id_order:
                item_path = f"{path}[{id_key}={item_id}]" if path else f"[{id_key}={item_id}]"
                in_base = item_id in base_map
                in_ours = item_id in ours_map
                in_theirs = item_id in theirs_map

                if not in_base:
                    if in_ours and not in_theirs:
                        merged_list.append(ours_map[item_id])
                    elif in_theirs and not in_ours:
                        merged_list.append(theirs_map[item_id])
                    else:
                        res = merge_3way({}, ours_map[item_id], theirs_map[item_id], item_path)
                        merged_list.append(res.data)
                        conflicts.extend(res.conflicts)
                else:
                    base_item = base_map[item_id]
                    if not in_ours and not in_theirs:
                        continue
                    elif not in_ours and in_theirs:
                        if theirs_map[item_id] == base_item:
                            continue
                        conflicts.append(Conflict(item_path, base_item, None, theirs_map[item_id]))
                        merged_list.append(theirs_map[item_id])
                    elif in_ours and not in_theirs:
                        if ours_map[item_id] == base_item:
                            continue
                        conflicts.append(Conflict(item_path, base_item, ours_map[item_id], None))
                        merged_list.append(ours_map[item_id])
                    else:
                        res = merge_3way(base_item, ours_map[item_id], theirs_map[item_id], item_path)
                        merged_list.append(res.data)
                        conflicts.extend(res.conflicts)

            return MergeResult(merged_list, conflicts)

        # Fallback for scalar lists or non-identifiable lists:
        # If one side changed and the other did not (already handled above),
        # but if both changed differently, report conflict.
        conflicts = [Conflict(path or "root", base, ours, theirs)]
        return MergeResult(ours, conflicts)

    # 4C. Conflicting scalar or mismatched types
    conflicts = [Conflict(path or "root", base, ours, theirs)]
    return MergeResult(ours, conflicts)
