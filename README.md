# Grove — Semantic Version Control for Config Files

**Grove** is a git-inspired version control system designed specifically for structured configuration files (JSON, YAML, TOML). Rather than treating files as flat lines of text, Grove parses configurations into hierarchical semantic trees and versions, diffs, and merges them by meaning.

---

## Why Grove is Different from Git

Traditional Git diffs and merges operate strictly on text lines. In modern configuration management (Kubernetes manifests, OpenAPI specs, microservice configs, CI/CD pipelines), line-based version control creates constant friction:

| Problem in Git | How Grove Solves It |
|---|---|
| **Key Reordering**: Reordering keys in a dictionary or JSON object triggers false diffs and line conflicts. | **Key-Order Independence**: Dictionaries are diffed by semantic keys, not line order. Reordering keys produces `(no semantic differences)`. |
| **Formatting / Whitespace**: Changing indentations, trailing commas, or blank lines registers as changes. | **Canonical Determinism**: Content is normalized into canonical data structures before hashing. Deterministic canonical representations ensure identical hashes regardless of formatting. |
| **List Reordering**: Swapping items in a list of services triggers lines of text churn. | **Identity-Aware List Matching**: Lists of objects containing identifying fields (`id`, `name`, `key`, `_id`) are matched by identity instead of index position. Moving an item in a list is not a change. |
| **File Conflicts on Disjoint Fields**: Two engineers modifying different sections of the same config file cause line conflicts in Git. | **Field-Level 3-Way Merge**: Three-way merges recurse into nested trees. If Branch A edits `server.port` and Branch B edits `database.host`, Grove auto-merges both cleanly. Real conflicts are surfaced only when the exact same field is modified differently. |

---

## System Architecture

Grove implements a content-addressed storage system in `.grove/`:

```
.grove/
├── objects/        # Content-addressed blobs (SHA-1 of canonical bytes)
├── commits/        # Commit metadata (tree, parents, author, timestamp, message)
├── refs/heads/     # Branch pointers (main, feature, etc.)
└── HEAD            # Symbolic reference (ref: refs/heads/main) or commit hash
```

### Core Pipeline
1. **Canonicalization (`grove/canonical.py`)**: Parses JSON/YAML/TOML into native structures and provides `canonical_bytes(data)` for deterministic hashing.
2. **Object Store (`grove/objectstore.py`)**: Stores blobs and commits immutably by hash.
3. **Semantic Diff (`grove/diff.py`)**: Generates tree-aware semantic diffs (`+`, `-`, `~`) with identity resolution.
4. **Semantic Merge (`grove/merge.py`)**: Performs recursive 3-way field-level merges against the Lowest Common Ancestor (LCA).
5. **Repository Engine (`grove/repo.py`)**: High-level repository management, branching, checkout, fast-forward/no-op resolution, and commit history.
6. **CLI (`grove/cli.py`)**: Command-line interface accessible directly or via `python -m grove`.

---

## Installation & Requirements

Grove requires Python 3.9+ and minimal dependencies:

```bash
pip install -r requirements.txt
```

`requirements.txt`:
```
pyyaml>=6.0
toml>=0.10.0
pytest>=7.0.0
```

---

## Python API Reference

The canonical parser provides consistent API methods:

```python
from grove.canonical import parse_file, parse_str, serialize, canonical_bytes, hash_data

# Parse files and strings into native structures
config = parse_file("config.yaml")
data = parse_str('{"port": 8080}', format_hint="json")

# Deterministic canonical serialization (order-independent byte hash)
raw_bytes = canonical_bytes(config)
digest = hash_data(config)

# Format structured data back into target configuration syntax
yaml_text = serialize(config, format_hint="yaml")
```

*(Aliases `load`, `loads`, and `dump` are also supported for compatibility).*

---

## CLI Command Reference

| Command | Description |
|---|---|
| `grove init [path]` | Initialize a new repository in `.grove` |
| `grove commit [-m MESSAGE] [files...]` | Snapshot tracked config files into a new commit |
| `grove log [-n LIMIT]` | View commit history starting from HEAD |
| `grove diff <commit_a> [commit_b]` | Semantic diff between two commits (defaults to HEAD) |
| `grove diff-working [file]` | Compare working directory against HEAD commit |
| `grove branch [name]` | List existing branches or create a new branch |
| `grove checkout <branch_or_commit>` | Switch branches and restore files in working tree |
| `grove merge <branch>` | Three-way merge target branch into current HEAD branch |

---

## Step-by-Step Demo Walkthrough

### 1. Initialize and Commit Base Configuration
```bash
grove init
cat << 'EOF' > config.yaml
server:
  host: "0.0.0.0"
  port: 8080
database:
  host: "localhost"
  port: 5432
EOF

grove commit -m "Initial commit with base config"
```

### 2. Create Feature Branch and Modify Field A
```bash
grove branch feature
grove checkout feature

# Edit server.port to 9090
grove commit -m "Update server port to 9090"
```

### 3. Switch Back to Main and Modify Unrelated Field B
```bash
grove checkout main

# Edit database.host to "db.internal"
grove commit -m "Update database host to db.internal"
```

### 4. Clean Three-Way Merge
```bash
grove merge feature
# Output: Merge successful: a3b4c5d
```
Grove analyzes the Lowest Common Ancestor (LCA) and detects that `main` changed `database.host` while `feature` changed `server.port`. Both changes are merged cleanly into `config.yaml` without conflict!

### 5. Conflict Detection
If both branches change `server.port` to different values (e.g., `9100` vs `9200`), Grove flags the conflict without silently picking a winner:
```
Automatic merge failed. Fix conflicts and commit the result.
Found 1 merge conflict(s):
  - Field 'server.port':
      base:   8080
      ours:   9100
      theirs: 9200
```

### 6. Semantic diff-working with Reordered Keys
Reordering keys or altering whitespace in `config.yaml`:
```yaml
database:
  port: 5432
  host: "localhost"
server:
  port: 8080
  host: "0.0.0.0"
```
Running `grove diff-working` results in:
```
(no semantic differences)
```

---

## Known Limitations

### Scalar-List and Anonymous-List Merging
Lists of plain scalars (such as string tags `["alpha", "beta"]` or integers `[1, 2, 3]`), as well as lists of dictionaries without an identifying field (`id`, `name`, `key`, or `_id`), cannot be positionally three-way merged unambiguously.

In these cases, Grove cannot determine whether an element inserted at index 0 is intended to prepend, replace, or reorder elements. Therefore, Grove falls back to taking whichever side actually changed the list relative to base:
- If only one side modified the scalar list, that side's modification is taken.
- If both sides simultaneously modified the scalar list differently, Grove surfaces a conflict on that list.

**Example in practice**:
Given `base: ["web", "worker"]`, if *Ours* updates to `["web", "worker", "cron"]` while *Theirs* updates to `["api", "web", "worker"]`, Grove flags a conflict on the list rather than guessing the positional interleaving. For identity-based list merging without conflicts, assign an `id` or `name` field to each list item.

---

## Running Tests

Run the full pytest suite:

```bash
pytest -v tests/
```
