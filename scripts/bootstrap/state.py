"""Persistent state for bootstrap secrets that can only be captured once.

Stored in scripts/bootstrap/.state.{env}.json (gitignored).
"""

import json
import secrets
from pathlib import Path


def _state_path(env: str) -> Path:
    return Path(f"scripts/bootstrap/.state.{env}.json")


def load_state(env: str) -> dict:
    path = _state_path(env)
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_state(env: str, state: dict) -> None:
    path = _state_path(env)
    path.write_text(json.dumps(state, indent=2))
    # Restrict to owner read/write — file contains production secrets.
    path.chmod(0o600)


def get_or_generate_jwt_secret(env: str, state: dict) -> str:
    """Return a stable JWT secret, generating and persisting one if absent."""
    if state.get("app_jwt_secret"):
        return state["app_jwt_secret"]
    secret = secrets.token_hex(32)
    state["app_jwt_secret"] = secret
    return secret
