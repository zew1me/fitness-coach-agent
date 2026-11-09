"""Configuration, allowlist handling, and the combined reachability check."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from fitness_coach_reachability_check.modules import discover_modules, reachable_modules
from fitness_coach_reachability_check.routes import (
    ClientPath,
    Route,
    client_paths,
    fastapi_routes,
    next_routes,
)

Kind = Literal["endpoint", "client-path", "module"]
_KINDS: tuple[Kind, ...] = ("endpoint", "client-path", "module")
_TABLE = "reachability-check"


class ConfigError(ValueError):
    """Raised when the ``[tool.reachability-check]`` table is missing or malformed."""


@dataclass(frozen=True)
class Allow:
    kind: Kind
    target: str
    reason: str


@dataclass(frozen=True)
class Config:
    entrypoints: list[str]
    python_packages: list[str]
    module_scopes: list[str]
    client_roots: list[str]
    next_app_dir: str
    allow: list[Allow] = field(default_factory=list)


@dataclass(frozen=True)
class Finding:
    kind: Kind | Literal["stale-allow"]
    target: str
    detail: str


def _str_list(table: dict[str, Any], key: str) -> list[str]:
    value = table.get(key)
    if not (isinstance(value, list) and value and all(isinstance(v, str) for v in value)):
        raise ConfigError(f"[tool.{_TABLE}].{key} must be a non-empty list of strings")
    return value


def _parse_allow(entry: Any) -> Allow:  # raw TOML value, validated below
    if not isinstance(entry, dict):
        raise ConfigError(f"[[tool.{_TABLE}.allow]] entries must be tables")
    kind, target, reason = entry.get("kind"), entry.get("target"), entry.get("reason")
    if kind not in _KINDS:
        raise ConfigError(f"allow entry {target!r}: kind must be one of {', '.join(_KINDS)}")
    if not isinstance(target, str) or not target:
        raise ConfigError("allow entry is missing a target")
    # AGENTS.md: every suppression carries an explanation of why the rule does not apply.
    if not isinstance(reason, str) or not reason.strip():
        raise ConfigError(f"allow entry {target!r} must explain itself in a non-empty reason")
    return Allow(kind, target, reason.strip())


def _read_table(pyproject: Path) -> dict[str, Any]:
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"cannot read {pyproject}: {error}") from error
    tool = data.get("tool")
    table = tool.get(_TABLE) if isinstance(tool, dict) else None
    if not isinstance(table, dict):
        raise ConfigError(f"{pyproject} has no [tool.{_TABLE}] table")
    return table


def _check_layout(root: Path, config: Config) -> None:
    """Reject paths that would make a check silently scan nothing and pass vacuously."""
    dirs = [*config.python_packages, *config.module_scopes, *config.client_roots]
    for rel in [*dirs, config.next_app_dir]:
        if not (root / rel).is_dir():
            raise ConfigError(f"[tool.{_TABLE}] directory {rel!r} does not exist under {root}")
    for rel in config.entrypoints:
        if not (root / rel).is_file():
            raise ConfigError(f"[tool.{_TABLE}] entrypoint {rel!r} does not exist under {root}")
    packages = [Path(p) for p in config.python_packages]
    for scope in config.module_scopes:
        if not any(Path(scope).is_relative_to(p) for p in packages):
            raise ConfigError(f"module scope {scope!r} is outside every python_packages entry")


def load_config(pyproject: Path) -> Config:
    table = _read_table(pyproject)
    allow = table.get("allow", [])
    if not isinstance(allow, list):
        raise ConfigError(f"[tool.{_TABLE}].allow must be an array of tables")
    app_dir = table.get("next_app_dir")
    if not isinstance(app_dir, str) or not app_dir:
        raise ConfigError(f"[tool.{_TABLE}].next_app_dir must be a non-empty string")
    return Config(
        entrypoints=_str_list(table, "entrypoints"),
        python_packages=_str_list(table, "python_packages"),
        module_scopes=_str_list(table, "module_scopes"),
        client_roots=_str_list(table, "client_roots"),
        next_app_dir=app_dir,
        allow=[_parse_allow(e) for e in allow],
    )


def _endpoint_findings(routes: list[Route], refs: list[ClientPath]) -> list[Finding]:
    return [
        Finding("endpoint", r.path, f"{','.join(r.methods)} at {r.location} has no client caller")
        for r in routes
        if not any(r.matches(ref) for ref in refs)
    ]


def _client_findings(routes: list[Route], refs: list[ClientPath]) -> list[Finding]:
    findings: dict[str, Finding] = {}
    for ref in refs:
        if not any(r.matches(ref) for r in routes) and ref.display not in findings:
            detail = f"referenced at {ref.location} matches no FastAPI or Next.js route"
            findings[ref.display] = Finding("client-path", ref.display, detail)
    return list(findings.values())


def _module_findings(root: Path, config: Config) -> list[Finding]:
    modules = discover_modules(root, config.python_packages)
    reached = reachable_modules(root, modules, config.entrypoints)
    findings = []
    for name, rel in sorted(modules.items()):
        in_scope = any(rel.as_posix().startswith(f"{s.rstrip('/')}/") for s in config.module_scopes)
        if in_scope and rel.name != "__init__.py" and name not in reached:
            detail = f"{rel.as_posix()} is not imported on any path from the entrypoints"
            findings.append(Finding("module", rel.as_posix(), detail))
    return findings


def _apply_allowlist(findings: list[Finding], allow: list[Allow]) -> list[Finding]:
    flagged = {(f.kind, f.target) for f in findings}
    allowed = {(a.kind, a.target) for a in allow}
    remaining = [f for f in findings if (f.kind, f.target) not in allowed]
    # An allow entry that no longer matches anything would silently mask a future regression.
    remaining.extend(
        Finding("stale-allow", a.target, f"{a.kind} allow entry no longer matches a finding")
        for a in allow
        if (a.kind, a.target) not in flagged
    )
    return remaining


def run_check(root: Path, config: Config) -> list[Finding]:
    """Return every unallowlisted finding for the repository at ``root``."""
    _check_layout(root, config)
    api_routes = fastapi_routes(root, config.entrypoints)
    all_routes = api_routes + next_routes(root, config.next_app_dir)
    refs = client_paths(root, config.client_roots)
    findings = [
        *_endpoint_findings(api_routes, refs),
        *_client_findings(all_routes, refs),
        *_module_findings(root, config),
    ]
    return _apply_allowlist(findings, config.allow)
