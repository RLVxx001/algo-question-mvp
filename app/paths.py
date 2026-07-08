from __future__ import annotations

from pathlib import Path


def resolve_under(root: Path, *parts: str) -> Path | None:
    root_path = root.resolve()
    path = root_path.joinpath(*parts).resolve()
    try:
        path.relative_to(root_path)
    except ValueError:
        return None
    return path
