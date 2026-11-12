"""Deployment-only HTTP transport, OAuth refresh/revoke, and UI-message SSE decoding."""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import replace
from http import HTTPStatus
from typing import Any

import httpx

from fitness_coach_cli.credentials import CoachError, CredentialStore, Tokens, normalize_base_url

CLIENT_ID = "fitness-coach-cli"


def check_response(response: httpx.Response) -> None:
    if response.is_success:
        return
    # Never echo remote bodies: they can contain bearer tokens, signed URLs, or diagnostics.
    hint = (
        "Log in again."
        if response.status_code in {400, 401, 403}
        else "Check the target and retry."
    )
    raise CoachError(f"Deployment request failed. {hint}", status=response.status_code)


class CoachClient:
    def __init__(
        self, base_url: str, store: CredentialStore, *, transport: httpx.BaseTransport | None = None
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self.store = store
        self._generation: str | None = None
        self.http = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(30, read=180),
            follow_redirects=False,
            transport=transport,
        )

    def __enter__(self) -> CoachClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.http.close()

    def exchange_code(self, code: str, verifier: str, redirect_uri: str) -> None:
        response = self.http.post(
            "/api/oauth/token",
            json={
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
            },
        )
        check_response(response)
        tokens = Tokens.parse(response.json())
        with self.store.locked():
            self.store.save(self.base_url, tokens)
            self._generation = tokens.generation

    def _access_token(self, rejected: Tokens | None = None) -> Tokens:
        with self.store.locked():
            tokens = self.store.get(self.base_url)
            expected = rejected.generation if rejected is not None else self._generation
            if expected is not None and tokens.generation != expected:
                raise CoachError(
                    "Login changed in another terminal; request was not sent or retried."
                )
            self._generation = tokens.generation
            # A concurrent refresh is reusable, but a different login must never inherit a write.
            rejected_access = rejected.access_token if rejected is not None else None
            if not tokens.needs_refresh() and tokens.access_token != rejected_access:
                return tokens
            response = self.http.post(
                "/api/oauth/token",
                json={
                    "grant_type": "refresh_token",
                    "client_id": CLIENT_ID,
                    "refresh_token": tokens.refresh_token,
                },
            )
            check_response(response)
            replacement = replace(Tokens.parse(response.json()), generation=tokens.generation)
            self.store.save(self.base_url, replacement)
            return replacement

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Retry only an explicit 401 once; never replay a write after a transport error."""
        token = self._access_token()
        response = self.http.request(
            method, path, headers={"Authorization": f"Bearer {token.access_token}"}, **kwargs
        )
        if response.status_code == HTTPStatus.UNAUTHORIZED:
            token = self._access_token(rejected=token)
            response = self.http.request(
                method, path, headers={"Authorization": f"Bearer {token.access_token}"}, **kwargs
            )
        check_response(response)
        return response.json()

    @contextmanager
    def stream(self, payload: dict[str, Any]):
        token = self._access_token()
        for attempt in range(2):
            with self.http.stream(
                "POST",
                "/api/chat",
                json=payload,
                headers={
                    "Authorization": f"Bearer {token.access_token}",
                    "Accept": "text/event-stream",
                },
            ) as response:
                if response.status_code != HTTPStatus.UNAUTHORIZED or attempt:
                    check_response(response)
                    if "text/event-stream" not in response.headers.get("content-type", ""):
                        raise CoachError("Deployment returned a non-streaming chat response.")
                    yield response
                    return
            token = self._access_token(rejected=token)

    def logout(self) -> bool:
        """Revoke the starting grant; return whether that login is also removed locally."""
        token = self._access_token()
        response = self.http.post(
            "/api/oauth/revoke", json={"client_id": CLIENT_ID, "token": token.access_token}
        )
        check_response(response)
        if response.json().get("revoked") is not True:
            raise CoachError("Grant was not revoked; credentials retained. Try login/logout again.")
        with self.store.locked():
            try:
                current = self.store.get(self.base_url)
            except CoachError as error:
                if error.status == HTTPStatus.UNAUTHORIZED:
                    return True  # Another logout already removed this target.
                raise
            if current.generation != token.generation:
                return False  # A new login must survive an old logout finishing late.
            self.store.save(self.base_url, None)
            return True


def _decode_event(payload: str) -> dict[str, Any]:
    event = json.loads(payload)
    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
        raise CoachError("Invalid chat stream event.")
    if event["type"] in {"error", "abort"}:
        raise CoachError("Coach stream failed. Check chat history before retrying.")
    return event


def stream_events(response: httpx.Response):
    """Decode SSE data frames, preserving all UI events and surfacing incomplete turns."""
    data: list[str] = []
    finished = False
    for line in response.iter_lines():
        if line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
        elif not line and data:
            payload = "\n".join(data)
            data.clear()
            if payload == "[DONE]":
                if not finished:
                    raise CoachError("Chat ended without a completed turn. Check chat history.")
                return
            event = _decode_event(payload)
            finished = finished or event["type"] == "finish"
            yield event
    if not finished or data:
        raise CoachError("Chat stream ended early. Check chat history before retrying.")
