# Repository scripts and local CLI tools

This repository contains both small maintenance scripts and durable local tools. Choose the
smallest structure that matches the expected lifecycle, but do not grow a user-facing CLI inside
`scripts/`.

## Use a one-shot script

Put code in `scripts/` when it is repository glue rather than a product-like tool. A one-shot script
should normally meet all of these conditions:

- it performs a narrow development, migration, or maintenance task;
- it uses the root project's existing dependencies, or only the standard library;
- it has no durable user configuration, credentials, state, or independent release surface;
- it is not expected to be installed, scheduled, or invoked regularly by users;
- a dedicated console entry point would add more structure than value.

Examples include local database wrappers, a one-time data repair, or a small file transformation.
Shell is appropriate for simple process orchestration. Python one-shot scripts should expose a
`main()` and keep logic testable where the risk warrants tests.

Do not add a third-party CLI framework to a direct script just to parse two arguments. If the script
starts accumulating subcommands, third-party runtime dependencies, configuration, credentials, or
regular users, promote it to a workspace tool.

## Use a uv workspace tool

Create `tools/<tool-name>/` as a uv workspace package when any of these apply:

- the command is a durable or user-facing local CLI;
- it is run repeatedly or by cron, launchd, or another scheduler;
- it owns third-party dependencies that should not become application runtime dependencies;
- it has named configuration, credentials, cached state, or multiple commands;
- it benefits from an installable console entry point or an independent package boundary;
- it is likely to grow independently of the web/API application.

Python workspace CLIs should use Typer unless there is a documented reason not to. Keep business
logic callable separately from Typer command functions so tests do not need to spawn a process.

Use this layout:

```text
tools/<tool-name>/
├── pyproject.toml
├── config.example.toml       # when the tool is configurable
├── src/<import_package>/
│   ├── __init__.py
│   └── cli.py
└── tests/
    └── test_cli.py
```

The package `pyproject.toml` must declare its own runtime dependencies and a `[project.scripts]`
entry point. Register it in the root workspace:

```toml
[tool.uv.workspace]
members = ["tools/*"]
```

If root-level pytest, Ruff, ty, or vulture checks cover the tool, wire the workspace package into the
root development group through `[tool.uv.sources]` and include its source/tests in the relevant tool
configuration. Regenerate `uv.lock` in the same change. CI and local hooks must exercise the tool;
do not create an untested workspace island.

Run a workspace command from the repository root with:

```bash
uv run --package <distribution-name> <console-command> --help
```

## Promotion checklist

When promoting an existing direct script into `tools/`:

1. Move its implementation, tests, and example configuration together.
2. Add the workspace package metadata and console entry point.
3. Replace argparse or ad hoc parsing with Typer for a durable Python CLI.
4. Update documentation and automation to use `uv run --package ...`.
5. Update the shared lockfile and all static-analysis/test discovery paths.
6. Remove the old script entry point rather than maintaining two interfaces.
