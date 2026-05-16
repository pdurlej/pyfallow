from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .config import PythonConfig
from .paths import is_test_path, matches_any, relpath


COMMON_SOURCE_DIRS = ["src", "app", "backend", "server", "service"]


def discover_source_roots(config: PythonConfig) -> list[Path]:
    root = config.root
    resolved_root = root.resolve()
    if config.roots:
        roots = []
        for item in config.roots:
            path = root / item
            if path.exists() and not path.is_symlink() and _is_inside(path.resolve(), resolved_root):
                roots.append(path.resolve())
        return _dedupe_preserving_order(roots)

    candidates: list[Path] = []
    for name in COMMON_SOURCE_DIRS:
        path = root / name
        if (
            path.is_dir()
            and not path.is_symlink()
            and _is_inside(path.resolve(), resolved_root)
            and any(path.rglob("*.py"))
        ):
            candidates.append(path.resolve())

    root_py_files = list(root.glob("*.py"))
    package_dirs = [
        item
        for item in root.iterdir()
        if item.is_dir()
        and not item.is_symlink()
        and _is_inside(item.resolve(), resolved_root)
        and not item.name.startswith(".")
        and (item / "__init__.py").exists()
        and not matches_any(item.name + "/", config.ignore)
    ] if root.exists() else []
    if root_py_files or package_dirs or not candidates:
        candidates.append(root.resolve())

    return _dedupe_preserving_order(sorted(candidates, key=_source_root_specificity_key))


def _source_root_specificity_key(path: Path) -> tuple[int, str]:
    resolved = path.resolve()
    return (-len(resolved.parts), resolved.as_posix())


def _dedupe_preserving_order(paths: Iterable[Path]) -> list[Path]:
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = path.as_posix()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def discover_python_files(config: PythonConfig, source_roots: list[Path]) -> list[Path]:
    root = config.root
    resolved_root = root.resolve()
    files: dict[str, Path] = {}
    for source_root in source_roots:
        if (
            not source_root.exists()
            or source_root.is_symlink()
            or not _is_inside(source_root.resolve(), resolved_root)
        ):
            continue
        for path in source_root.rglob("*.py"):
            if path.is_symlink() or not _is_inside(path.resolve(), resolved_root):
                continue
            relative = relpath(path, root)
            if matches_any(relative, config.ignore):
                continue
            if _ignored_by_parent(relative, config.ignore):
                continue
            if not config.namespace_packages and not _regular_package_path(path, source_root):
                continue
            if not config.include_tests and is_test_path(relative):
                # Tests are excluded from dead-code reporting by default, but dependency
                # classification still benefits from seeing explicitly configured test roots.
                pass
            files[relative] = path.resolve()
    return [files[key] for key in sorted(files)]


def _ignored_by_parent(relative: str, patterns: list[str]) -> bool:
    parts = relative.split("/")
    prefixes = ["/".join(parts[:index]) + "/" for index in range(1, len(parts))]
    return any(matches_any(prefix, patterns) for prefix in prefixes)


def _regular_package_path(path: Path, source_root: Path) -> bool:
    parent = path.parent
    if parent == source_root:
        return True
    try:
        relative_parent = parent.relative_to(source_root)
    except ValueError:
        return False
    current = source_root
    for part in relative_parent.parts:
        current = current / part
        if not (current / "__init__.py").exists():
            return False
    return True


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
