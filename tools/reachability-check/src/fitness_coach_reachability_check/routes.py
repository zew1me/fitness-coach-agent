"""Collect server routes and client-side API path references, and match them."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from fitness_coach_reachability_check.ts_strings import string_segments

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})
_CLIENT_SUFFIXES = (".ts", ".tsx")
# Client paths are same-origin references; absolute URLs point at third parties, not our server.
_PATH_PREFIXES = ("/api/", "/.well-known/")
_PATH_END = re.compile(r"[?#\s]")
_ROUTE_PARAM = re.compile(r"\{([^}/]+)\}")
_NEXT_CATCH_ALL = re.compile(r"\[\[?\.\.\.[^\]]+\]\]?")
_NEXT_PARAM = re.compile(r"\[[^\]/]+\]")


@dataclass(frozen=True)
class Route:
    path: str
    methods: tuple[str, ...]
    location: str

    def matches(self, ref: ClientPath) -> bool:
        if _route_regex(self.path).fullmatch(ref.path):
            return True
        # `/api/x/${id}` is only known to start with `/api/x/`; it reaches any route under it.
        return ref.interpolated and self.path.startswith(ref.path)


@dataclass(frozen=True)
class ClientPath:
    path: str
    interpolated: bool
    location: str


def _route_regex(path: str) -> re.Pattern[str]:
    """Compile a route; ``{name}`` matches one segment, ``{name:path}`` or ``{rest...}`` several."""
    parts: list[str] = []
    pos = 0
    for param in _ROUTE_PARAM.finditer(path):
        parts.append(re.escape(path[pos : param.start()]))
        name = param.group(1)
        parts.append(".+" if name.endswith(("...", ":path")) else "[^/]+")
        pos = param.end()
    parts.append(re.escape(path[pos:]))
    return re.compile("".join(parts))


def _decorator_route(decorator: ast.expr) -> tuple[str, str] | None:
    """Return ``(method, path)`` for an ``@app.<method>("/path")`` decorator."""
    if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)):
        return None
    method = decorator.func.attr
    if method not in _HTTP_METHODS or not decorator.args:
        return None
    first = decorator.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return method.upper(), first.value
    return None


def fastapi_routes(root: Path, entrypoints: list[str]) -> list[Route]:
    """Read ``@app.<method>(path)`` decorators from the entrypoint modules' source."""
    by_path: dict[str, tuple[list[str], str]] = {}
    for rel in entrypoints:
        tree = ast.parse((root / rel).read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for decorator in node.decorator_list:
                found = _decorator_route(decorator)
                if found is None:
                    continue
                method, path = found
                methods, _ = by_path.setdefault(path, ([], f"{rel}:{decorator.lineno}"))
                methods.append(method)
    return [Route(path, tuple(methods), loc) for path, (methods, loc) in by_path.items()]


def next_routes(root: Path, app_dir: str) -> list[Route]:
    """Derive URL paths from Next.js App Router ``route.ts`` handlers."""
    base = root / app_dir
    routes = []
    for handler in sorted(base.rglob("route.ts")):
        parts = [p for p in handler.parent.relative_to(base).parts if not p.startswith("(")]
        path = "/" + "/".join(parts)
        path = _NEXT_CATCH_ALL.sub("{rest...}", path)
        path = _NEXT_PARAM.sub("{param}", path)
        routes.append(Route(path, ("NEXT",), handler.relative_to(root).as_posix()))
    return routes


def _client_path(text: str, interpolated: bool) -> tuple[str, bool] | None:
    if not text.startswith(_PATH_PREFIXES):
        return None
    cut = _PATH_END.search(text)
    if cut:
        return text[: cut.start()].rstrip("/") or "/", False
    if interpolated:
        return text, True
    return text.rstrip("/"), False


def client_paths(root: Path, client_roots: list[str]) -> list[ClientPath]:
    """Find same-origin API paths in string literals under the client source roots."""
    refs = []
    for client_root in client_roots:
        for source in sorted((root / client_root).rglob("*")):
            if source.suffix not in _CLIENT_SUFFIXES or "node_modules" in source.parts:
                continue
            rel = source.relative_to(root).as_posix()
            for segment in string_segments(source.read_text(encoding="utf-8")):
                found = _client_path(segment.text, segment.interpolated)
                if found:
                    refs.append(ClientPath(found[0], found[1], f"{rel}:{segment.line}"))
    return refs
