from __future__ import annotations

from pathlib import Path

import pytest
from click.utils import strip_ansi
from fitness_coach_reachability_check.check import ConfigError, load_config, run_check
from fitness_coach_reachability_check.cli import app
from fitness_coach_reachability_check.routes import ClientPath, Route
from fitness_coach_reachability_check.ts_strings import INTERPOLATION, string_segments
from typer.testing import CliRunner

_CONFIG = """
[tool.reachability-check]
entrypoints = ["api/index.py"]
python_packages = ["api", "backend"]
module_scopes = ["backend/engine", "backend/services"]
client_roots = ["app", "lib"]
next_app_dir = "app"
"""

_API = """
from fastapi import FastAPI

from backend.services import course

app = FastAPI()


@app.post("/api/engine/process-uploaded-file")
async def process():
    from backend.engine.tss import compute

    return compute()


@app.get("/api/items/{item_id}")
async def item(item_id: str):
    return item_id
"""

_CLIENT = """
// "/api/engine/process-uploaded-file" in a comment must not count as a caller.
export const upload = (base: string, id: string) => [
  fetch(`${base}/api/engine/process-uploaded-file`),
  fetch(`/api/items/${id}`),
  fetch("/api/chat"),
  /\\/api\\/regex-only/.test(base),
];
"""


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _write(tmp_path, "pyproject.toml", _CONFIG)
    _write(tmp_path, "api/__init__.py", "")
    _write(tmp_path, "api/index.py", _API)
    _write(tmp_path, "backend/__init__.py", "")
    _write(tmp_path, "backend/engine/__init__.py", "")
    _write(tmp_path, "backend/engine/tss.py", "def compute():\n    return 1\n")
    _write(tmp_path, "backend/services/__init__.py", "")
    _write(tmp_path, "backend/services/course.py", "from ..engine import course_profile\n")
    _write(tmp_path, "backend/engine/course_profile.py", "")
    _write(tmp_path, "app/api/chat/route.ts", "export function POST() {}\n")
    _write(tmp_path, "lib/client.ts", _CLIENT)
    return tmp_path


def _findings(root: Path) -> set[tuple[str, str]]:
    findings = run_check(root, load_config(root / "pyproject.toml"))
    return {(f.kind, f.target) for f in findings}


def test_clean_tree_has_no_findings(repo: Path) -> None:
    # Covers a template literal with a base-URL prefix, a path parameter, a Next.js route, a
    # function-local import and a relative import, all of which must count as reachable.
    assert _findings(repo) == set()


def test_entrypoint_outside_packages_is_traversed(repo: Path) -> None:
    _write(repo, "server.py", _API)
    _write(repo, "pyproject.toml", _CONFIG.replace('"api/index.py"', '"server.py"'))
    assert _findings(repo) == set()


def test_missing_entrypoint_is_rejected(repo: Path) -> None:
    (repo / "api/index.py").unlink()
    with pytest.raises(ConfigError, match="entrypoint.*does not exist"):
        _findings(repo)


def test_module_imported_only_by_tests_is_flagged(repo: Path) -> None:
    # Regression fixture for #417: backend/engine/course_analyzer.py shipped with zero callers
    # outside its own tests, and no existing check reported it.
    _write(repo, "backend/engine/course_analyzer.py", "def analyze_cycling_climb():\n    pass\n")
    _write(repo, "tests/test_course_analyzer.py", "from backend.engine import course_analyzer\n")
    assert _findings(repo) == {("module", "backend/engine/course_analyzer.py")}


@pytest.mark.parametrize("scope", ["backend/./engine", "backend/services/../engine"])
def test_module_scope_paths_are_normalized(repo: Path, scope: str) -> None:
    _write(repo, "backend/engine/dead.py", "")
    _write(repo, "pyproject.toml", _CONFIG.replace("backend/engine", scope))
    assert _findings(repo) == {("module", "backend/engine/dead.py")}


def test_endpoint_without_client_caller_is_flagged(repo: Path) -> None:
    _write(
        repo,
        "api/index.py",
        _API + '\n\n@app.post("/api/engine/orphan")\nasync def o():\n    pass\n',
    )
    assert _findings(repo) == {("endpoint", "/api/engine/orphan")}


def test_path_mentioned_only_in_comment_or_regex_is_not_a_caller(repo: Path) -> None:
    _write(
        repo,
        "lib/client.ts",
        _CLIENT.replace("fetch(`${base}/api/engine/process-uploaded-file`),", ""),
    )
    assert _findings(repo) == {("endpoint", "/api/engine/process-uploaded-file")}


def test_client_path_without_route_is_flagged(repo: Path) -> None:
    _write(repo, "lib/extra.ts", 'export const missing = fetch("/api/engine/typo?x=1");\n')
    assert _findings(repo) == {("client-path", "/api/engine/typo")}


def test_allow_entry_suppresses_and_goes_stale(repo: Path) -> None:
    _write(repo, "backend/engine/dead.py", "")
    allow = '\n[[tool.reachability-check.allow]]\nkind = "module"\ntarget = "{}"\nreason = "why"\n'
    _write(repo, "pyproject.toml", _CONFIG + allow.format("backend/engine/dead.py"))
    assert _findings(repo) == set()
    (repo / "backend/engine/dead.py").unlink()
    assert _findings(repo) == {("stale-allow", "backend/engine/dead.py")}


def test_allow_entry_requires_a_reason(repo: Path) -> None:
    allow = '\n[[tool.reachability-check.allow]]\nkind = "module"\ntarget = "x.py"\nreason = " "\n'
    _write(repo, "pyproject.toml", _CONFIG + allow)
    with pytest.raises(ConfigError, match="reason"):
        load_config(repo / "pyproject.toml")


def test_string_segments_keep_template_text_around_interpolations() -> None:
    segments = string_segments("const a = `x${`/api/${b}`}y`; // '/api/c'\n")
    assert [s.text for s in segments] == [f"/api/{INTERPOLATION}", f"x{INTERPOLATION}y"]


def test_string_segments_decode_escapes() -> None:
    segments = string_segments('fetch("/api/foo\\/items"); fetch(`/api/a\\`b`);')
    assert [s.text for s in segments] == ["/api/foo/items", "/api/a`b"]


@pytest.mark.parametrize("quote", ['"', "`"])
@pytest.mark.parametrize(
    ("escaped", "decoded"),
    [
        (r"/api/foo\x2fitems", "/api/foo/items"),
        (r"/api/foo\u002fitems", "/api/foo/items"),
        (r"/api/foo\u{2f}items", "/api/foo/items"),
        ("/api/foo\\\n/items", "/api/foo/items"),
        ("/api/foo\\\r\n/items", "/api/foo/items"),
        (r"/api/foo\titems", "/api/foo\titems"),
    ],
)
def test_javascript_escapes_are_decoded(quote: str, escaped: str, decoded: str) -> None:
    source = f"fetch({quote}{escaped}{quote});"
    assert [s.text for s in string_segments(source)] == [decoded]


@pytest.mark.parametrize(
    "prefix",
    [
        "const r = ",
        "const r = /* note */ ",
        "return ",
        "if (enabled) ",
        "if (enabled) /* note */ ",
        "while ((enabled && check(value))) ",
        "for (let i = 0; i < 3; i++) ",
        "for (const value of ",
    ],
)
def test_regex_literal_with_quotes_is_not_a_string(prefix: str) -> None:
    source = prefix + """/["'\\/api\\/regex-only'"]/;\nfetch("/api/real");\n"""
    assert [s.text for s in string_segments(source)] == ["/api/real"]


@pytest.mark.parametrize(
    "operand",
    [
        "total",
        "fn()",
        "(total + tax)",
        "of",
        "opts.of",
        "x.in",
        "opts?.return",
        "obj.if()",
        "obj. /* note */ return",
    ],
)
def test_division_does_not_hide_a_real_string(operand: str) -> None:
    source = f'{operand} / count; fetch("/api/real");'
    assert [s.text for s in string_segments(source)] == ["/api/real"]


@pytest.mark.parametrize(
    "source",
    [
        '<div>Don\'t miss this {fetch("/api/real")}</div>',
        '<>Don\'t miss this {fetch("/api/real")}</>',
        '<div><span>Don\'t</span>{fetch("/api/real")}</div>',
        '<div>"/api/not-a-caller" {fetch("/api/real")}</div>',
        '<div title="hello">Don\'t {fetch("/api/real")}</div>',
        '<Button onClick={() => fetch("/api/real")}>Don\'t</Button>',
        '<Button onClick={() => fetch("/api/real")} />',
        'const x = <T>value; fetch("/api/real");',
        'const f = <T extends "/api/real">(x: T) => x;',
        'const x = a < b; fetch("/api/real");',
    ],
)
def test_jsx_text_does_not_swallow_javascript_literals(source: str) -> None:
    paths = [s.text for s in string_segments(source) if s.text.startswith("/api/")]
    assert paths == ["/api/real"]


def _client(path: str) -> ClientPath:
    return ClientPath(path.replace("${id}", INTERPOLATION), "lib/x.ts:1")


@pytest.mark.parametrize(
    ("route", "client", "expected"),
    [
        ("/api/users/{user_id}/events/{event_id}", "/api/users/me/events/${id}", True),
        ("/api/items/{item_id}/delete", "/api/items/${id}/details", False),
        ("/api/items/{item_id}/details", "/api/items/${id}/details", True),
        ("/api/items/report-{year}", "/api/items/report-${id}", True),
        ("/api/items/report-{year}", "/api/items/report-2026", True),
        ("/api/items/report-{year}.json", "/api/items/report-2026.json", True),
        ("/api/items/report-{year}.json", "/api/items/report-2026.csv", False),
        ("/api/items/report-{year}", "/api/items/report-", False),
        ("/api/items/report-{year}", "/api/items/other-2026", False),
        ("/api/files/{file_path:path}", "/api/files/a/b/c", True),
        ("/api/files/{file_path:path}", "/api/files", True),
        ("/api/files/{file_path:path}", "/api/files/", True),
        ("/api/files/{file_path:path}", "/api/other/", False),
        ("/api/files/{rest...}", "/api/files/", False),
        ("/api/files/{file_path:path}", "/api/${id}", True),
        ("/api/files/{file_path:path}", "/api/wrong/${id}", False),
        ("/api/search/{rest...}", "/api/${id}", True),
        ("/api/a/b/c", "/api/${id}", True),
        ("/api/a/b/c", "/api/z/${id}", False),
        ("/api/trailing/", "/api/trailing", True),
        ("/api/search/{rest...?}", "/api/search", True),
        ("/api/search/{rest...?}", "/api/search/x/y", True),
        ("/api/search/{rest...}", "/api/search", False),
    ],
)
def test_route_matching(route: str, client: str, expected: bool) -> None:
    assert Route(route, ("GET",), "api/index.py:1").matches(_client(client)) is expected


def test_next_optional_catch_all_route(repo: Path) -> None:
    _write(repo, "app/api/search/[[...slug]]/route.ts", "export function GET() {}\n")
    _write(repo, "lib/search.ts", 'export const s = fetch("/api/search");\n')
    assert _findings(repo) == set()


@pytest.mark.parametrize(
    "decorator",
    [
        '@app.api_route("/api/multi", methods=["GET", "POST"])',
        '@app.api_route(path="/api/multi", methods=["GET"])',
        '@app.api_route("/api/multi")',
    ],
)
def test_api_route_decorator_is_collected(repo: Path, decorator: str) -> None:
    _write(repo, "api/index.py", _API + f"\n{decorator}\nasync def multi():\n    pass\n")
    assert _findings(repo) == {("endpoint", "/api/multi")}
    _write(repo, "lib/multi.ts", 'fetch("/api/multi");')
    assert _findings(repo) == set()


def test_nonliteral_api_route_is_not_collected(repo: Path) -> None:
    extra = '\n@app.api_route(dynamic_path, methods=["GET"])\nasync def multi():\n    pass\n'
    _write(repo, "api/index.py", _API + extra)
    assert _findings(repo) == set()


def test_keyword_path_decorator_is_a_route(repo: Path) -> None:
    extra = '\n\n@app.get(path="/api/engine/keyword")\nasync def k():\n    pass\n'
    _write(repo, "api/index.py", _API + extra)
    assert _findings(repo) == {("endpoint", "/api/engine/keyword")}


@pytest.mark.parametrize(
    ("binding", "condition", "expected"),
    [
        ("import typing", "typing.TYPE_CHECKING", {("module", "backend/engine/hints.py")}),
        ("import typing as t", "t.TYPE_CHECKING", {("module", "backend/engine/hints.py")}),
        ("from typing import TYPE_CHECKING as TC", "TC", {("module", "backend/engine/hints.py")}),
        ("import config", "config.TYPE_CHECKING", set()),
        ("TYPE_CHECKING = True", "TYPE_CHECKING", set()),
        ("from typing import TYPE_CHECKING\nTYPE_CHECKING = True", "TYPE_CHECKING", set()),
        ("import typing\ntyping = config", "typing.TYPE_CHECKING", set()),
        (
            "from typing import TYPE_CHECKING\nfrom config import TYPE_CHECKING",
            "TYPE_CHECKING",
            set(),
        ),
    ],
)
def test_only_typing_guards_exclude_imports(
    repo: Path, binding: str, condition: str, expected: set[tuple[str, str]]
) -> None:
    _write(repo, "backend/engine/hints.py", "")
    guard = f"{binding}\nif {condition}:\n    from backend.engine import hints\n"
    _write(repo, "api/index.py", guard + _API)
    assert _findings(repo) == expected


def test_function_parameter_shadowing_typing_does_not_hide_runtime_import(repo: Path) -> None:
    _write(repo, "backend/engine/hints.py", "")
    guard = (
        "import typing\n"
        "def load(typing):\n"
        "    if typing.TYPE_CHECKING:\n"
        "        from backend.engine import hints\n"
    )
    _write(repo, "api/index.py", guard + _API)
    assert _findings(repo) == set()


@pytest.mark.parametrize(
    ("subject", "pattern", "expected"),
    [
        ("True", "TYPE_CHECKING", set()),
        ("True", "True as TYPE_CHECKING", set()),
        ("[True]", "[*TYPE_CHECKING]", set()),
        ('{"enabled": True}', "{**TYPE_CHECKING}", set()),
        ('{"enabled": True}', '{"enabled": TYPE_CHECKING}', set()),
        ("True", "_", {("module", "backend/engine/hints.py")}),
        ("[True]", "[*_]", {("module", "backend/engine/hints.py")}),
        ('{"enabled": True}', '{"enabled": _}', {("module", "backend/engine/hints.py")}),
    ],
)
def test_pattern_capture_rebinding_typing_guard(
    repo: Path, subject: str, pattern: str, expected: set[tuple[str, str]]
) -> None:
    _write(repo, "backend/engine/hints.py", "")
    guard = (
        "from typing import TYPE_CHECKING\n"
        f"match {subject}:\n"
        f"    case {pattern}:\n"
        "        pass\n"
        "if TYPE_CHECKING:\n"
        "    from backend.engine import hints\n"
    )
    compile(guard, "pattern-capture-fixture", "exec")
    _write(repo, "api/index.py", guard + _API)
    assert _findings(repo) == expected


def test_type_checking_import_does_not_make_a_module_reachable(repo: Path) -> None:
    _write(repo, "backend/engine/hints.py", "")
    _write(repo, "backend/engine/runtime_hints.py", "")
    guard = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from backend.engine import hints\n"
        "else:\n"
        "    from backend.engine import runtime_hints\n"
    )
    _write(repo, "api/index.py", guard + _API)
    assert _findings(repo) == {("module", "backend/engine/hints.py")}


@pytest.mark.parametrize(
    ("config", "message"),
    [
        (_CONFIG.replace('"app", "lib"', '"app", "missing"'), "does not exist"),
        (_CONFIG.replace('"backend/services"]', '"backend/services", "tools"]'), "outside"),
        (_CONFIG.replace('"app", "lib"', '"app", ""'), "non-blank"),
        (_CONFIG.replace('"backend/services"]', '"backend/services", " "]'), "non-blank"),
        (_CONFIG.replace('next_app_dir = "app"', 'next_app_dir = " "'), "next_app_dir"),
    ],
)
def test_layout_errors_fail_instead_of_scanning_nothing(
    repo: Path, config: str, message: str
) -> None:
    _write(repo, "tools/x.py", "")
    _write(repo, "pyproject.toml", config)
    with pytest.raises(ConfigError, match=message):
        run_check(repo, load_config(repo / "pyproject.toml"))


def test_cli_exit_codes(repo: Path) -> None:
    runner = CliRunner()
    ok = runner.invoke(app, ["check", "--root", str(repo)])
    assert ok.exit_code == 0, strip_ansi(ok.output)

    _write(repo, "backend/services/orphan.py", "")
    failed = runner.invoke(app, ["check", "--root", str(repo)])
    assert failed.exit_code == 1
    assert "backend/services/orphan.py" in strip_ansi(failed.output)

    for broken in ("[tool.other]\n", "[tool\n", "tool = 1\n"):
        (repo / "pyproject.toml").write_text(broken, encoding="utf-8")
        assert runner.invoke(app, ["check", "--root", str(repo)]).exit_code == 2, broken
