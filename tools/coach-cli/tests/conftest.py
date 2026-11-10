from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from fitness_coach_cli import cli
from fitness_coach_cli.client import CoachClient
from fitness_coach_cli.credentials import Credentials, CredentialStore
from typer.testing import CliRunner, Result

BASE = "https://preview-abc.vercel.app"
NOW = 1_800_000_000.0

Handler = Callable[[httpx.Request], httpx.Response]


class FakeApi:
    """Route table behind an httpx.MockTransport; records every request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._routes: dict[tuple[str, str], list[Handler]] = {}
        self.transport = httpx.MockTransport(self._handle)

    def on(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        status: int = 200,
        handler: Handler | None = None,
    ) -> None:
        """Queue a response; the last queued one repeats once the queue is down to one."""
        handler = handler or (lambda _req: httpx.Response(status, json=json))
        self._routes.setdefault((method, path), []).append(handler)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        queue = self._routes.get((request.method, request.url.path))
        if not queue:
            return httpx.Response(404, json={"detail": f"unrouted {request.method} {request.url}"})
        handler = queue.pop(0) if len(queue) > 1 else queue[0]
        return handler(request)

    def to(self, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == path]

    @staticmethod
    def body(request: httpx.Request) -> Any:
        return json.loads(request.content)


def bundle(access: str = "acc-1", refresh: str = "ref-1", expires_in: int = 3600) -> dict[str, Any]:
    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_in": expires_in,
        "user_id": "user-1",
    }


@pytest.fixture
def api() -> FakeApi:
    return FakeApi()


@pytest.fixture
def creds_path(tmp_path: Path) -> Path:
    return tmp_path / "config" / "credentials.json"


@pytest.fixture
def store(creds_path: Path) -> CredentialStore:
    return CredentialStore(creds_path)


@pytest.fixture
def client(api: FakeApi, store: CredentialStore) -> CoachClient:
    return CoachClient(BASE, store, transport=api.transport, clock=lambda: NOW)


def seed(
    store: CredentialStore, *, expires_at: float = NOW + 3600, base: str = BASE
) -> Credentials:
    creds = Credentials("acc-1", "ref-1", expires_at, "user-1")
    store.save(base, creds)
    return creds


@pytest.fixture
def run(api: FakeApi, creds_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., Result]:
    monkeypatch.setattr(cli, "_transport", api.transport)
    monkeypatch.delenv("COACH_BASE_URL", raising=False)
    monkeypatch.delenv("COACH_CREDENTIALS_FILE", raising=False)
    runner = CliRunner()

    def invoke(*args: str, input: str | None = None, base: str | None = BASE) -> Result:
        prefix = ["--credentials-file", str(creds_path)]
        if base is not None:
            prefix += ["--base-url", base]
        return runner.invoke(cli.app, [*prefix, *args], input=input)

    return invoke


class Helpers:
    """Shared constants and builders; exposed as a fixture because tests cannot import conftest."""

    BASE = BASE
    NOW = NOW
    bundle = staticmethod(bundle)
    seed = staticmethod(seed)
    body = staticmethod(FakeApi.body)


@pytest.fixture
def h() -> type[Helpers]:
    return Helpers
