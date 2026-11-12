"""Walk the Python import graph from the deployed entrypoints."""

from __future__ import annotations

import ast
from collections import Counter, deque
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


def _binding_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
        return node.id
    if isinstance(node, ast.arg):
        return node.arg
    if isinstance(node, ast.alias):
        return node.asname or node.name.split(".")[0]
    if isinstance(
        node,
        ast.FunctionDef
        | ast.AsyncFunctionDef
        | ast.ClassDef
        | ast.ExceptHandler
        | ast.MatchAs
        | ast.MatchStar,
    ):
        return node.name
    if isinstance(node, ast.MatchMapping):
        return node.rest
    return None


def _rebound_names(tree: ast.Module) -> set[str]:
    bindings = Counter(filter(None, map(_binding_name, ast.walk(tree))))
    # Conservatively retain imports if an alias is rebound/shadowed anywhere in this module.
    # This avoids guessing at runtime guards without implementing Python name resolution.
    return {name for name, count in bindings.items() if count > 1}


def _typing_bindings(tree: ast.Module) -> tuple[set[str], set[str]]:
    names: set[str] = set()
    modules: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            modules.update(a.asname or a.name for a in node.names if a.name == "typing")
        elif isinstance(node, ast.ImportFrom) and node.module == "typing" and node.level == 0:
            names.update(a.asname or a.name for a in node.names if a.name == "TYPE_CHECKING")
    rebound = _rebound_names(tree)
    return names - rebound, modules - rebound


def _is_type_checking(test: ast.expr, names: set[str], modules: set[str]) -> bool:
    return (isinstance(test, ast.Name) and test.id in names) or (
        isinstance(test, ast.Attribute)
        and isinstance(test.value, ast.Name)
        and test.value.id in modules
        and test.attr == "TYPE_CHECKING"
    )


def _runtime_nodes(tree: ast.Module) -> list[ast.AST]:
    """All nodes except the bodies of `if TYPE_CHECKING:`, which never execute at runtime."""
    names, modules = _typing_bindings(tree)
    nodes: list[ast.AST] = []
    stack: list[ast.AST] = [tree]
    while stack:
        node = stack.pop()
        nodes.append(node)
        if isinstance(node, ast.If) and _is_type_checking(node.test, names, modules):
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
    # Entrypoints can live outside package scan roots, e.g. a repository-level server.py.
    modules = modules | {_module_name(Path(e)): Path(e) for e in entrypoints}
    queue = deque(_module_name(Path(e)) for e in entrypoints)
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
