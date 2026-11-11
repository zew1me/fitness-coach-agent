"""The single error type every command reports, as text or as ``{error, status, detail}``."""

from __future__ import annotations


class CoachError(Exception):
    def __init__(
        self,
        error: str,
        detail: str,
        *,
        status: int | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.error = error
        self.detail = detail
        self.status = status
        self.hint = hint

    def to_json(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "error": self.error,
            "status": self.status,
            "detail": self.detail,
        }
        if self.hint is not None:
            payload["hint"] = self.hint
        return payload
