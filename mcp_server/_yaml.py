"""Shared YAML loading helpers for PMD scaffolding."""

from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover - dependency varies by environment
    yaml = None
    _YAML_IMPORT_ERROR = exc
else:  # pragma: no cover - trivial branch
    _YAML_IMPORT_ERROR = None


def load_yaml_file(path: Path) -> Any:
    """Load a YAML document with a clear dependency error if PyYAML is absent."""
    if yaml is None:
        raise RuntimeError(
            "PyYAML is required to read PMD registry files. Install with: pip install pyyaml"
        ) from _YAML_IMPORT_ERROR

    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def dump_yaml_file(path: Path, data: Any) -> None:
    """Write a YAML document with stable formatting for PMD state files."""
    if yaml is None:
        raise RuntimeError(
            "PyYAML is required to write PMD registry files. Install with: pip install pyyaml"
        ) from _YAML_IMPORT_ERROR

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False)