"""Browser approval using the existing authorization-code flow with S256 PKCE."""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

from fitness_coach_cli.client import CLIENT_ID, CoachClient
from fitness_coach_cli.credentials import CoachError

SCOPES = "profile:read profile:write plans:read plans:write metrics:write"


@dataclass
class Approval:
    state: str
    path: str
    code: str | None = None
    denied: bool = False

    def capture(self, request_path: str) -> bool:
        """Ignore unrelated/forged requests without terminating the real login attempt."""
        parsed = urlsplit(request_path)
        query = parse_qs(parsed.query)
        states = query.get("state", [])
        if parsed.path != self.path or len(states) != 1:
            return False
        if not secrets.compare_digest(states[0].encode(), self.state.encode()):
            return False
        codes, errors = query.get("code", []), query.get("error", [])
        if len(codes) == 1 and not errors:
            self.code = codes[0]
            return True
        if len(errors) == 1 and not codes:
            self.denied = True
            return True
        return False


def _handler(approval: Approval) -> type[BaseHTTPRequestHandler]:
    class Callback(BaseHTTPRequestHandler):
        # A stalled local HTTP client must not defeat the overall login deadline.
        timeout = 5

        def do_GET(self) -> None:  # noqa: N802 -- stdlib HTTP handler requires this method name.
            accepted = approval.capture(self.path)
            self.send_response(200 if accepted else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
            )
            self.end_headers()
            self.wfile.write(
                b"Return to your terminal." if accepted else b"Invalid approval callback."
            )

        def log_message(self, format: str, *args: object) -> None:
            # Request paths contain the single-use authorization code; never write HTTP logs.
            _ = format, args

    return Callback


def callback_address(value: str) -> tuple[str, int, str]:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
    ):
        raise CoachError(
            "--callback-url must be http://127.0.0.1:PORT/path or http://localhost:PORT/path."
        )
    try:
        port = parsed.port if parsed.port is not None else 80
    except ValueError as error:
        raise CoachError("Invalid callback port.") from error
    return parsed.hostname, port, parsed.path


def browser_login(
    client: CoachClient,
    *,
    callback_url: str,
    open_browser: bool,
    timeout: int,
    announce: Callable[[str], None],
) -> None:
    """Listen only on IPv4 loopback; exchange a state-checked code with the exact callback URI."""
    hostname, port, path = callback_address(callback_url)
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    approval = Approval(state=secrets.token_urlsafe(32), path=path)
    with HTTPServer(("127.0.0.1", port), _handler(approval)) as server:
        redirect_uri = f"http://{hostname}:{server.server_port}{path}"
        authorize_url = (
            client.base_url
            + "/api/oauth/authorize?"
            + urlencode(
                {
                    "client_id": CLIENT_ID,
                    "redirect_uri": redirect_uri,
                    "response_type": "code",
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "scope": SCOPES,
                    "state": approval.state,
                    "prompt": "consent",
                }
            )
        )
        announce(f"Approve this CLI in your browser:\n{authorize_url}")
        if open_browser:
            webbrowser.open(authorize_url)
        server.timeout = 1
        deadline = time.monotonic() + timeout
        while approval.code is None and not approval.denied:
            if time.monotonic() >= deadline:
                raise CoachError("Approval timed out; run login again.")
            server.handle_request()
    if approval.denied:
        raise CoachError("Browser approval was denied; no credentials saved.")
    if approval.code is None:
        raise CoachError("Approval did not return an authorization code.")
    client.exchange_code(approval.code, verifier, redirect_uri)
