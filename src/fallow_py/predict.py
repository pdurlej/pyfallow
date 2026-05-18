from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field
from difflib import get_close_matches
from pathlib import Path
from typing import Any

from .analysis import analyze
from .boundaries import _matches as boundary_matches
from .config import PythonConfig
from .dependencies import FALLBACK_STDLIB, parse_dependency_declarations
from .paths import normalize_package_name, relpath
from .resolver import ModuleResolver


COMMON_THIRD_PARTY_IMPORTS = {
    "bs4",
    "celery",
    "click",
    "django",
    "fastapi",
    "flask",
    "httpx",
    "numpy",
    "pandas",
    "pydantic",
    "pytest",
    "requests",
    "sqlalchemy",
    "typer",
}


@dataclass(frozen=True, slots=True)
class PlannedImport:
    raw: str
    name: str
    alias: str | None = None


@dataclass(frozen=True, slots=True)
class ImportPrediction:
    raw: str
    import_name: str
    classification: str
    target_module: str | None = None
    imported_symbol: str | None = None
    distribution: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class HallucinatedImport:
    raw: str
    import_name: str
    reason: str
    similar: list[str] = field(default_factory=list)
    distribution: str | None = None


@dataclass(frozen=True, slots=True)
class CyclePrediction:
    raw: str
    import_name: str
    cycle_path: list[str]


@dataclass(frozen=True, slots=True)
class BoundaryViolation:
    raw: str
    import_name: str
    rule: str
    reason: str


@dataclass(frozen=True, slots=True)
class MissingDependency:
    raw: str
    import_name: str
    distribution: str
    reason: str


@dataclass(frozen=True, slots=True)
class VerifyResult:
    status: str
    file: str
    planned_imports: list[str]
    safe: list[ImportPrediction] = field(default_factory=list)
    decision_needed: list[ImportPrediction] = field(default_factory=list)
    hallucinated: list[HallucinatedImport] = field(default_factory=list)
    cycles_introduced: list[CyclePrediction] = field(default_factory=list)
    boundary_violations: list[BoundaryViolation] = field(default_factory=list)
    missing_dependencies: list[MissingDependency] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_import_spec(raw: str) -> PlannedImport:
    text = raw.strip()
    if " as " not in text:
        return PlannedImport(raw=raw, name=text)
    name, alias = text.rsplit(" as ", 1)
    return PlannedImport(raw=raw, name=name.strip(), alias=alias.strip() or None)


def verify_imports(
    config: PythonConfig,
    target_file: Path,
    planned_imports: list[str],
    report: dict[str, Any] | None = None,
) -> VerifyResult:
    report = report or analyze(config)
    importer_module, importer_path, importer_is_init = _target_module(config, report, target_file)
    module_index = {item["id"]: item for item in report.get("graphs", {}).get("modules", [])}
    module_paths = {module: item["path"] for module, item in module_index.items()}
    declarations = parse_dependency_declarations(config.root)
    declared = {normalize_package_name(name) for name in declarations.all}
    adjacency = _adjacency(report)

    safe: list[ImportPrediction] = []
    decision_needed: list[ImportPrediction] = []
    hallucinated: list[HallucinatedImport] = []
    cycles: list[CyclePrediction] = []
    boundaries: list[BoundaryViolation] = []
    missing: list[MissingDependency] = []

    for raw in planned_imports:
        spec = parse_import_spec(raw)
        absolute_name = _absolute_import_name(spec.name, importer_module, importer_is_init)
        if not absolute_name:
            hallucinated.append(
                HallucinatedImport(
                    raw=raw,
                    import_name=spec.name,
                    reason="empty import name cannot be verified",
                )
            )
            continue
        if absolute_name.endswith(".*"):
            decision_needed.append(
                ImportPrediction(
                    raw=raw,
                    import_name=spec.name,
                    classification="star-import",
                    target_module=absolute_name[:-2],
                    imported_symbol="*",
                    reason="star imports cannot be verified statically before edit",
                )
            )
            continue

        resolved = _resolve_local(absolute_name, module_index)
        if resolved:
            target_module, imported_symbol = resolved
            symbol_missing = _missing_symbol(module_index[target_module], imported_symbol)
            if symbol_missing:
                hallucinated.append(
                    HallucinatedImport(
                        raw=raw,
                        import_name=spec.name,
                        reason=f"module '{target_module}' has no symbol '{imported_symbol}'",
                        similar=_similar_symbols(imported_symbol or "", module_index[target_module]),
                        distribution=target_module,
                    )
                )
                continue
            blocked = False
            cycle_path = _cycle_path(importer_module, target_module, adjacency)
            if cycle_path:
                cycles.append(CyclePrediction(raw=raw, import_name=spec.name, cycle_path=cycle_path))
                blocked = True
            boundary = _boundary_violation(
                config,
                importer_module,
                importer_path,
                target_module,
                module_paths.get(target_module, ""),
                raw,
                spec.name,
            )
            if boundary:
                boundaries.append(boundary)
                blocked = True
            if not blocked:
                safe.append(
                    ImportPrediction(
                        raw=raw,
                        import_name=spec.name,
                        classification="local",
                        target_module=target_module,
                        imported_symbol=imported_symbol,
                    )
                )
            continue

        top = absolute_name.split(".", 1)[0]
        if _is_stdlib(top):
            safe.append(
                ImportPrediction(
                    raw=raw,
                    import_name=spec.name,
                    classification="stdlib",
                    target_module=absolute_name,
                )
            )
            continue

        distribution = _distribution_for_import(absolute_name, config)
        if distribution in declared:
            safe.append(
                ImportPrediction(
                    raw=raw,
                    import_name=spec.name,
                    classification="third-party",
                    distribution=distribution,
                )
            )
        elif _looks_like_third_party(absolute_name, distribution, config):
            missing.append(
                MissingDependency(
                    raw=raw,
                    import_name=spec.name,
                    distribution=distribution,
                    reason=f"third-party package '{distribution}' is not declared as a dependency",
                )
            )
        else:
            hallucinated.append(
                HallucinatedImport(
                    raw=raw,
                    import_name=spec.name,
                    reason=f"module '{absolute_name}' not found",
                    similar=_similar_modules(absolute_name, module_index),
                )
            )

    status = (
        "issues_found"
        if hallucinated or cycles or boundaries or missing or decision_needed
        else "ok"
    )
    return VerifyResult(
        status=status,
        file=_target_path(config, target_file),
        planned_imports=list(planned_imports),
        safe=safe,
        decision_needed=decision_needed,
        hallucinated=hallucinated,
        cycles_introduced=cycles,
        boundary_violations=boundaries,
        missing_dependencies=missing,
    )


def _target_module(config: PythonConfig, report: dict[str, Any], target_file: Path) -> tuple[str, str, bool]:
    path = target_file if target_file.is_absolute() else config.root / target_file
    target_path = _target_path(config, target_file)
    source_roots = [config.root / item for item in report.get("analysis", {}).get("source_roots", [])]
    if not source_roots:
        source_roots = [config.root]
    resolver = ModuleResolver(config.root, source_roots)
    module, _, is_init = resolver.module_name_for_path(path)
    return module, target_path, is_init


def _target_path(config: PythonConfig, target_file: Path) -> str:
    root = config.root.resolve()
    path = (target_file if target_file.is_absolute() else root / target_file).resolve()
    try:
        return path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError(f"Target file is outside analysis root: {target_file}") from exc


def _absolute_import_name(name: str, importer_module: str, importer_is_init: bool) -> str:
    if not name.startswith("."):
        return name
    level = len(name) - len(name.lstrip("."))
    raw = name[level:]
    package = importer_module if importer_is_init else importer_module.rpartition(".")[0]
    package_parts = package.split(".") if package else []
    if level > 1:
        package_parts = package_parts[: max(0, len(package_parts) - level + 1)]
    base = ".".join(part for part in package_parts if part)
    return ".".join(part for part in (base, raw) if part)


def _resolve_local(
    import_name: str,
    module_index: dict[str, dict[str, Any]],
) -> tuple[str, str | None] | None:
    if import_name in module_index:
        return import_name, None
    parts = import_name.split(".")
    for index in range(len(parts) - 1, 0, -1):
        module = ".".join(parts[:index])
        if module in module_index:
            return module, ".".join(parts[index:])
    return None


def _missing_symbol(module: dict[str, Any], symbol: str | None) -> bool:
    if not symbol:
        return False
    return symbol not in _symbol_candidates(module)


def _symbol_candidates(module: dict[str, Any]) -> set[str]:
    symbols = {item["name"] for item in module.get("symbols", [])}
    exports = {item["name"] for item in module.get("exports", [])}
    return symbols | exports


def _similar_symbols(symbol: str, module: dict[str, Any]) -> list[str]:
    return get_close_matches(symbol, sorted(_symbol_candidates(module)), n=3, cutoff=0.45)


def _similar_modules(import_name: str, module_index: dict[str, dict[str, Any]]) -> list[str]:
    return get_close_matches(import_name, sorted(module_index), n=3, cutoff=0.6)


def _is_stdlib(top_level: str) -> bool:
    return top_level in (set(getattr(sys, "stdlib_module_names", set())) | FALLBACK_STDLIB)


def _distribution_for_import(import_name: str, config: PythonConfig) -> str:
    top = import_name.split(".", 1)[0]
    mapped = {key: normalize_package_name(value) for key, value in config.dependencies.import_map.items()}
    for key in sorted(mapped, key=len, reverse=True):
        if import_name == key or import_name.startswith(key + ".") or top == key:
            return mapped[key]
    return normalize_package_name(top)


def _looks_like_third_party(import_name: str, distribution: str, config: PythonConfig) -> bool:
    top = import_name.split(".", 1)[0]
    configured = {normalize_package_name(key) for key in config.dependencies.import_map}
    configured |= {normalize_package_name(value) for value in config.dependencies.import_map.values()}
    return (
        normalize_package_name(top) in COMMON_THIRD_PARTY_IMPORTS
        or distribution in COMMON_THIRD_PARTY_IMPORTS
        or normalize_package_name(top) in configured
        or distribution in configured
    )


def _adjacency(report: dict[str, Any]) -> dict[str, set[str]]:
    adjacency: dict[str, set[str]] = {
        item["id"]: set() for item in report.get("graphs", {}).get("modules", [])
    }
    for edge in report.get("graphs", {}).get("edges", []):
        adjacency.setdefault(edge["from"], set()).add(edge["to"])
        adjacency.setdefault(edge["to"], set())
    return adjacency


def _cycle_path(importer: str, target: str, adjacency: dict[str, set[str]]) -> list[str]:
    if importer == target:
        return [importer, importer]
    path_back = _path_between(target, importer, adjacency)
    if not path_back:
        return []
    return [importer, *path_back]


def _path_between(start: str, end: str, adjacency: dict[str, set[str]]) -> list[str]:
    queue: list[list[str]] = [[start]]
    seen = {start}
    while queue:
        path = queue.pop(0)
        current = path[-1]
        if current == end:
            return path
        for candidate in sorted(adjacency.get(current, set())):
            if candidate in seen:
                continue
            seen.add(candidate)
            queue.append([*path, candidate])
    return []


def _boundary_violation(
    config: PythonConfig,
    importer_module: str,
    importer_path: str,
    target_module: str,
    target_path: str,
    raw: str,
    import_name: str,
) -> BoundaryViolation | None:
    for rule in config.boundary_rules:
        if not any(boundary_matches(importer_path, importer_module, pattern) for pattern in rule.from_patterns):
            continue
        matched = next(
            (pattern for pattern in rule.disallow if boundary_matches(target_path, target_module, pattern)),
            None,
        )
        if matched:
            return BoundaryViolation(
                raw=raw,
                import_name=import_name,
                rule=rule.name,
                reason=f"boundary rule '{rule.name}' disallows importing {target_module}",
            )
    return None
