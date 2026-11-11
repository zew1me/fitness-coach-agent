"""Pin the coach CLI (tools/coach-cli) to the API it drives, so neither drifts silently."""

from fastapi.routing import APIRoute
from fitness_coach_cli import client as cli_client
from fitness_coach_cli.operations import OPERATIONS

import api.index as api_index
from backend.services import auth

_ENGINE_PREFIX = "/api/engine/"


def test_cli_catalog_matches_engine_routes() -> None:
    engine_routes = {
        route.path.removeprefix(_ENGINE_PREFIX): route.methods
        for route in api_index.app.routes
        if isinstance(route, APIRoute) and route.path.startswith(_ENGINE_PREFIX)
    }

    assert set(OPERATIONS) == set(engine_routes)
    # `coach call` always POSTs.
    assert all(methods == {"POST"} for methods in engine_routes.values())


def test_cli_and_server_agree_on_the_client_id() -> None:
    assert cli_client.CLI_CLIENT_ID == auth.CLI_CLIENT_ID
