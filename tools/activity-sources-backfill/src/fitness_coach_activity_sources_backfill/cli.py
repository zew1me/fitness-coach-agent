"""Explicit-target operator commands. All three commands update backfill progress state."""

import json
import os
from collections.abc import Callable
from typing import Annotated, cast

import httpx
import typer

from fitness_coach_activity_sources_backfill.backfill import (
    MAX_BATCH_SIZE,
    BackfillError,
    Result,
    RPCClient,
    Settings,
    run_backfill,
)

app = typer.Typer(help=__doc__, no_args_is_help=True)


@app.callback()
def configure(
    ctx: typer.Context,
    url: Annotated[
        str, typer.Option(envvar="BACKFILL_SUPABASE_URL", help="Explicit target origin.")
    ],
    confirm_target: Annotated[str, typer.Option(help="Acknowledge the exact target host[:port].")],
    timeout: Annotated[
        float, typer.Option(min=1, max=600, help="Per-RPC timeout in seconds.")
    ] = 60,
) -> None:
    try:
        ctx.obj = Settings.validated(
            url, confirm_target, os.environ.get("BACKFILL_SERVICE_ROLE_KEY", ""), timeout
        )
    except (BackfillError, ValueError) as exc:
        # URL parsing errors must not echo a URL that could contain embedded credentials.
        message = str(exc) if isinstance(exc, BackfillError) else "Invalid target URL."
        typer.echo(message, err=True)
        raise typer.Exit(2) from None


def _emit(result: Result) -> None:
    typer.echo(json.dumps({"operation": type(result).__name__.lower(), **result.model_dump()}))


def _execute(ctx: typer.Context, action: Callable[[RPCClient], None]) -> None:
    settings = cast(Settings, ctx.obj)
    try:
        # No redirects or ambient proxy configuration: the service-role key belongs only
        # to the explicitly acknowledged origin. Credentials never appear in JSON output.
        with httpx.Client(
            base_url=settings.url,
            headers={"apikey": settings.key, "Authorization": f"Bearer {settings.key}"},
            timeout=settings.timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            action(RPCClient(client))
    except BackfillError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None


@app.command()
def preflight(ctx: typer.Context) -> None:
    """Validate all source mappings and Intervals identities; records preflight_at."""
    _execute(ctx, lambda rpc: _emit(rpc.preflight()))


@app.command()
def run(
    ctx: typer.Context,
    batch_size: Annotated[int, typer.Option(min=1, max=MAX_BATCH_SIZE)] = 500,
    max_batches: Annotated[int, typer.Option(min=1, help="Bound work per invocation.")] = 10_000,
) -> None:
    """Preflight, resume batches, repair holes by rewind, and verify twice. Emits JSONL."""

    def action(rpc: RPCClient) -> None:
        for result in run_backfill(rpc, batch_size=batch_size, max_batches=max_batches):
            _emit(result)

    _execute(ctx, action)


@app.command()
def verify(ctx: typer.Context) -> None:
    """Run one global verification pass; exit 1 unless completion is established.

    This is NOT read-only: it can rewind the cursor and update completion markers.
    """

    def action(rpc: RPCClient) -> None:
        result = rpc.verify()
        _emit(result)
        if not result.complete:
            raise BackfillError("Backfill is not complete; run to resume and verify again.")

    _execute(ctx, action)
