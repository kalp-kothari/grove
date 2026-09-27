"""Canonical parsing and deterministic serialization for config files."""

import json
import os
import hashlib
from typing import Any, Optional
import yaml
import toml


SUPPORTED_EXTENSIONS = {
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
}


def is_config_file(filepath: str) -> bool:
    """Return True if the filepath has a supported configuration extension."""
    _, ext = os.path.splitext(filepath)
    return ext.lower() in SUPPORTED_EXTENSIONS


def detect_format(filepath: str, format_hint: Optional[str] = None) -> str:
    """Detect file format based on hint or file extension."""
    if format_hint:
        normalized = format_hint.lower().lstrip(".")
        if normalized in ("yml", "yaml"):
            return "yaml"
        if normalized in ("json", "toml"):
            return normalized
        raise ValueError(f"Unsupported format hint: {format_hint}")

    _, ext = os.path.splitext(filepath)
    fmt = SUPPORTED_EXTENSIONS.get(ext.lower())
    if not fmt:
        raise ValueError(f"Unsupported or unrecognized file extension for: {filepath}")
    return fmt


def _normalize_data(data: Any) -> Any:
    """Recursively convert tuples/sets to lists for deterministic handling."""
    if isinstance(data, dict):
        return {str(k): _normalize_data(v) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [_normalize_data(item) for item in data]
    return data


def canonical_bytes(data: Any) -> bytes:
    """Return deterministic, order-independent canonical bytes for structured data.

    Sorting all dictionary keys recursively and using uniform JSON serialization
    guarantees that differing key order or whitespace produces the exact same bytes.
    """
    normalized = _normalize_data(data)
    encoded = json.dumps(
        normalized,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return encoded.encode("utf-8")


def hash_data(data: Any) -> str:
    """Return the SHA-1 hexadecimal digest for the canonical representation of data."""
    return hashlib.sha1(canonical_bytes(data)).hexdigest()


def parse_str(content: str, format_hint: str) -> Any:
    """Parse a configuration string in JSON, YAML, or TOML format."""
    fmt = detect_format("", format_hint)
    try:
        if fmt == "json":
            parsed = json.loads(content) if content.strip() else {}
            return parsed if parsed is not None else {}
        if fmt == "yaml":
            parsed = yaml.safe_load(content)
            return parsed if parsed is not None else {}
        if fmt == "toml":
            return toml.loads(content) if content.strip() else {}
        raise ValueError(f"Unknown format: {fmt}")
    except (json.JSONDecodeError, yaml.YAMLError, toml.TomlDecodeError) as e:
        raise ValueError(f"Failed to parse {fmt.upper()}: {e}") from e


def parse_file(filepath: str, format_hint: Optional[str] = None) -> Any:
    """Read and parse a configuration file from disk."""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Configuration file not found: '{filepath}'")
    if os.path.isdir(filepath):
        raise IsADirectoryError(f"Cannot parse directory as configuration file: '{filepath}'")
    fmt = detect_format(filepath, format_hint)
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    try:
        return parse_str(content, fmt)
    except ValueError as e:
        raise ValueError(f"Failed to parse configuration file '{filepath}': {e}") from e


def serialize(data: Any, format_hint: str) -> str:
    """Serialize structured Python data into formatted configuration string."""
    fmt = detect_format("", format_hint)
    if data is None:
        data = {}
    if fmt == "json":
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if fmt == "yaml":
        return yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
    if fmt == "toml":
        return toml.dumps(data)
    raise ValueError(f"Unknown format: {fmt}")


# Naming consistency aliases
load = parse_file
loads = parse_str
dump = serialize
