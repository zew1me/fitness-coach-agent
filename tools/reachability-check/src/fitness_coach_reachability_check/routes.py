"""Collect server routes and client-side API path references, and match them."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from fitness_coach_reachability_check.ts_strings import INTERPOLATION, string_segments

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})
_CLIENT_SUFFIXES = (".ts", ".tsx")
# Client paths are same-origin references; absolute URLs point at third parties, not our server.
_PATH_PREFIXES = ("/api/", "/.well-known/")
_PATH_END = re.compile(r"[?#\s]")
_ROUTE_PARAM = re.compile(r"\{[^}/]+\}")
_NEXT_OPTIONAL_CATCH_ALL = re.compile(r"\[\[\.\.\.[^\]]+\]\]")
_NEXT_CATCH_ALL = re.compile(r"\[\.\.\.[^\]]+\]")
_NEXT_PARAM = re.compile(r"\[[^\]/]+\]")
# Route-path tails that absorb any number of segments: FastAPI `{p:path}`, Next `[...p]`, and the
# optional Next `[[...p]]`, which also matches zero.
_MULTI_TAIL = ("{rest...}", "{rest...?}")


@dataclass(frozen=True)
class Route:
    path: str
    methods: tuple[str, ...]
    location: str

    def matches(self, ref: ClientPath) -> bool:
        """Compare segment by segment; a route parameter or a caller interpolation is dynamic."""
        route = _segments(self.path)
        client = _segments(ref.path)
        tail = route[-1]
        if tail.endswith(":path}") or tail in _MULTI_TAIL:
            minimum = len(route) - (1 if tail == "{rest...?}" else 0)
            return len(client) >= minimum and _all_match(route[:-1], client[: len(route) - 1])
        if client[-1] == INTERPOLATION and len(route) >= len(client):
            # `/api/${path}` may expand to several segments at runtime.
            return _all_match(route[: len(client) - 1], client[:-1])
        return len(route) == len(client) and _all_match(route, client)


@dataclass(frozen=True)
class ClientPath:
    path: str
    location: str

    @property
    def display(self) -> str:
        return self.path.replace(INTERPOLATION, "${…}")


def _segments(path: str) -> list[str]:
    # Trailing slashes are not significant on either side of the comparison.
    return (path.rstrip("/") or "/").split("/")


def _segment_matches(route_segment: str, client_segment: str) -> bool:
    if route_segment == client_segment or _ROUTE_PARAM.fullmatch(route_segment):
        return True
    if INTERPOLATION not in client_segment:
        return False
    # `events-${id}` matches a static route segment that has the same literal parts around it.
    pattern = ".*".join(re.escape(part) for part in client_segment.split(INTERPOLATION))
    return re.fullmatch(pattern, route_segment) is not None


def _all_match(route: list[str], client: list[str]) -> bool:
    return len(route) == len(client) and all(map(_segment_matches, route, client))


def _decorator_route(decorator: ast.expr) -> tuple[str, str] | None:
    """Return ``(method, path)`` for an ``@app.<method>("/path")`` decorator."""
    if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)):
        return None
    method = decorator.func.attr
    if method not in _HTTP_METHODS:
        return None
    keyword = next((k.value for k in decorator.keywords if k.arg == "path"), None)
    first = decorator.args[0] if decorator.args else keyword
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
        path = _NEXT_OPTIONAL_CATCH_ALL.sub("{rest...?}", path)
        path = _NEXT_CATCH_ALL.sub("{rest...}", path)
        path = _NEXT_PARAM.sub("{param}", path)
        routes.append(Route(path, ("NEXT",), handler.relative_to(root).as_posix()))
    return routes


def _client_path(text: str) -> str | None:
    # A leading interpolation is the base URL, as in `${baseUrl}/api/chat/model-state`.
    text = text.lstrip(INTERPOLATION)
    if not text.startswith(_PATH_PREFIXES):
        return None
    cut = _PATH_END.search(text)
    return text[: cut.start()] if cut else text


def client_paths(root: Path, client_roots: list[str]) -> list[ClientPath]:
    """Find same-origin API paths in string literals under the client source roots."""
    refs = []
    for client_root in client_roots:
        for source in sorted((root / client_root).rglob("*")):
            if source.suffix not in _CLIENT_SUFFIXES or "node_modules" in source.parts:
                continue
            rel = source.relative_to(root).as_posix()
            for segment in string_segments(source.read_text(encoding="utf-8")):
                path = _client_path(segment.text)
                if path:
                    refs.append(ClientPath(path, f"{rel}:{segment.line}"))
    return refs
