"""Walk the Python import graph from the deployed entrypoints."""

from __future__ import annotations

import ast
from collections import deque
from pathlib import Path


def _module_name(rel: Path) -> str:
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def discover_modules(root: Path, packages: list[str]) -> dict[str, Path]:
    """Map dotted module names to files for every ``.py`` file under ``packages``."""
    found: dict[str, Path] = {}
    for package in packages:
        for path in sorted((root / package).rglob("*.py")):
            rel = path.relative_to(root)
            if "__pycache__" not in rel.parts:
                found[_module_name(rel)] = rel
    return found


def _resolve_from(node: ast.ImportFrom, current: str, is_package: bool) -> str:
    if node.level == 0:
        return node.module or ""
    base = current.split(".")
    # A package's own name is its base for `.`; a plain module's base is its parent package.
    drop = node.level - 1 if is_package else node.level
    base = base[: len(base) - drop] if drop else base
    return ".".join([*base, node.module] if node.module else base)


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _runtime_nodes(tree: ast.Module) -> list[ast.AST]:
    """All nodes except the bodies of `if TYPE_CHECKING:`, which never execute at runtime."""
    nodes: list[ast.AST] = []
    stack: list[ast.AST] = [tree]
    while stack:
        node = stack.pop()
        nodes.append(node)
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            stack.extend([node.test, *node.orelse])
        else:
            stack.extend(ast.iter_child_nodes(node))
    return nodes


def _imported_names(tree: ast.Module, current: str, is_package: bool) -> set[str]:
    """Every module an import in ``tree`` may load at runtime, including function-local ones."""
    names: set[str] = set()
    for node in _runtime_nodes(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            target = _resolve_from(node, current, is_package)
            names.add(target)
            # `from backend.engine import tss` loads the submodule `backend.engine.tss`.
            names.update(f"{target}.{alias.name}" for alias in node.names)
    return names


def _with_parents(name: str) -> list[str]:
    # Importing `a.b.c` executes `a/__init__.py` and `a/b/__init__.py` first.
    parts = name.split(".")
    return [".".join(parts[:i]) for i in range(1, len(parts) + 1)]


def reachable_modules(root: Path, modules: dict[str, Path], entrypoints: list[str]) -> set[str]:
    """Breadth-first walk of in-repo imports starting from the entrypoint files."""
    by_path = {path.as_posix(): name for name, path in modules.items()}
    queue = deque(by_path[e] for e in entrypoints if e in by_path)
    seen: set[str] = set(queue)
    while queue:
        current = queue.popleft()
        rel = modules[current]
        tree = ast.parse((root / rel).read_text(encoding="utf-8"), filename=str(rel))
        for name in _imported_names(tree, current, rel.name == "__init__.py"):
            for candidate in _with_parents(name):
                if candidate in modules and candidate not in seen:
                    seen.add(candidate)
                    queue.append(candidate)
    return seen
