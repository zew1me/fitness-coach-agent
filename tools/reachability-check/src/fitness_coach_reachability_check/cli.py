"""Typer entry point for the reachability gate."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from fitness_coach_reachability_check.check import ConfigError, load_config, run_check

app = typer.Typer(
    help="Fail on server routes, client API paths, or backend modules that nothing reaches.",
    no_args_is_help=True,
)


@app.callback()
def _root() -> None:
    """Keep `check` as an explicit subcommand so later checks can be added beside it."""


@app.command()
def check(
    root: Annotated[
        Path,
        typer.Option(help="Repository root; configuration is read from its pyproject.toml."),
    ] = Path(),
) -> None:
    """Run every reachability check and exit non-zero on any unallowlisted finding."""
    try:
        findings = run_check(root, load_config(root / "pyproject.toml"))
    except ConfigError as error:
        typer.echo(f"reachability-check: {error}", err=True)
        raise typer.Exit(2) from error
    for finding in findings:
        typer.echo(f"{finding.kind}: {finding.target} — {finding.detail}", err=True)
    if findings:
        typer.echo(
            f"reachability-check: {len(findings)} finding(s). Remove the dead code, add the "
            "missing caller, or add a [[tool.reachability-check.allow]] entry with a reason.",
            err=True,
        )
        raise typer.Exit(1)
    typer.echo("reachability-check: no unreachable endpoints, client paths, or modules.")


def main() -> None:
    app()
