"""Small helpers for atomic file writes.

Several modules write JSON state through a temporary file and then replace the
target.  Keeping that pattern in one place avoids subtle differences (suffixes,
cleanup, creating parent directories) and gives a typed, reusable abstraction.
"""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any


def _tmp_path(path: Path) -> Path:
    suffix = path.suffix + ".tmp" if path.suffix else ".tmp"
    return path.with_suffix(suffix)


def atomic_write_text(path: str | Path, text: str, *, encoding: str = "utf-8") -> bool:
    """Write *text* to *path* atomically, returning False on an OSError."""
    destination = Path(path)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = _tmp_path(destination)
        temporary.write_text(text, encoding=encoding)
        temporary.replace(destination)
        return True
    except OSError:
        with contextlib.suppress(OSError):
            _tmp_path(destination).unlink(missing_ok=True)
        return False


def atomic_write_json(path: str | Path, value: Any, *,
                     ensure_ascii: bool = False, indent: int | None = 2,
                     encoding: str = "utf-8") -> bool:
    """Serialize *value* as JSON and write it atomically."""
    return atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=ensure_ascii, indent=indent),
        encoding=encoding,
    )

