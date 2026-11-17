"""The single error type every layer raises; the CLI renders it and sets the exit code."""

from __future__ import annotations

from typing import Any


class CoachError(Exception):
    """A failed CLI operation, carrying the machine-readable fields printed under ``--json``."""

    def __init__(self, error: str, detail: Any = None, *, status: int | None = None) -> None:
        super().__init__(f"{error}: {detail}" if detail else error)
        self.error = error
        self.detail = detail
        self.status = status

    def as_dict(self) -> dict[str, Any]:
        return {"error": self.error, "status": self.status, "detail": self.detail}
