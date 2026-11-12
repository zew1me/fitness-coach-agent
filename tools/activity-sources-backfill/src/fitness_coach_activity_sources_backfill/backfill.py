"""Typed RPC boundary and restart-safe orchestration; no application ingestion logic."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_BATCH_SIZE = 10_000
REQUIRED_CLEAN_PASSES = 2


class BackfillError(RuntimeError):
    """An operator-visible failure whose message contains no credentials or row data."""


class Result(BaseModel):
    model_config = ConfigDict(strict=True)


class Preflight(Result):
    ok: bool
    source_counts: dict[str, int]


class Batch(Result):
    processed: int = Field(ge=0)
    drained: bool


class Verification(Result):
    holes: int = Field(ge=0)
    live_source_errors: int = Field(ge=0)
    capture_errors: int = Field(ge=0)
    drained: bool
    clean_passes: int = Field(ge=0, le=2)
    complete: bool

    def require_consistent(self) -> None:
        clean = not (self.holes or self.live_source_errors or self.capture_errors)
        if self.complete and not (
            clean and self.drained and self.clean_passes == REQUIRED_CLEAN_PASSES
        ):
            raise BackfillError("Inconsistent verification response; completion was not accepted.")

    def require_repairable(self) -> None:
        # Missing sources also count as live-source errors. Let SQL's rewind heal
        # those first; a conflicting captured source must never be overwritten.
        if self.capture_errors or (self.live_source_errors and not self.holes):
            raise BackfillError(
                "Verification found conflicting evidence; investigate before resuming."
            )


@dataclass(frozen=True)
class Settings:
    url: str
    key: str = field(repr=False)
    timeout: float = 60

    @classmethod
    def validated(cls, url: str, confirm_target: str, key: str, timeout: float) -> "Settings":
        parsed = urlsplit(url)
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or (parsed.scheme != "https" and not (local and parsed.scheme == "http"))
        ):
            raise BackfillError("Use an HTTPS Supabase origin (HTTP is allowed only on loopback).")
        if confirm_target != parsed.netloc:
            raise BackfillError("--confirm-target must exactly match the URL host[:port].")
        if not key.strip():
            raise BackfillError("Set BACKFILL_SERVICE_ROLE_KEY in the environment, not on argv.")
        if timeout <= 0:
            raise BackfillError("Timeout must be positive.")
        return cls(url.rstrip("/"), key, timeout)


class BackfillRPC(Protocol):
    def preflight(self) -> Preflight: ...
    def batch(self, size: int) -> Batch: ...
    def verify(self) -> Verification: ...


class RPCClient:
    def __init__(self, client: httpx.Client) -> None:
        self.client = client

    def _call[T: Result](self, name: str, payload: dict[str, int], result_type: type[T]) -> T:
        try:
            response = self.client.post(
                f"/rest/v1/rpc/activity_sources_backfill_{name}", json=payload
            )
            response.raise_for_status()
            return result_type.model_validate(response.json())
        except httpx.HTTPStatusError as exc:
            # PostgREST error details can include activity data; never echo response bodies.
            raise BackfillError(
                f"{name} RPC returned HTTP {exc.response.status_code}. "
                "Check credentials, applied migrations, and database diagnostics privately."
            ) from None
        except httpx.RequestError:
            # A response can be lost after commit. Do not blindly retry; the next run reads
            # the server checkpoint, and a batch's inserts/checkpoint commit together.
            raise BackfillError(
                f"{name} RPC transport failure; commit status is unknown. "
                "Rerun to resume from the database checkpoint."
            ) from None
        except (ValidationError, ValueError):
            raise BackfillError(f"Invalid {name} RPC response; refusing to advance.") from None

    def preflight(self) -> Preflight:
        result = self._call("preflight", {}, Preflight)
        if not result.ok:
            raise BackfillError("Preflight rejected the dataset; no batch was started.")
        return result

    def batch(self, size: int) -> Batch:
        result = self._call("batch", {"p_batch_size": size}, Batch)
        if result.processed > size or result.drained != (result.processed == 0):
            raise BackfillError("Inconsistent batch response; refusing to advance.")
        return result

    def verify(self) -> Verification:
        result = self._call("verify", {}, Verification)
        result.require_consistent()
        return result


def run_backfill(rpc: BackfillRPC, *, batch_size: int, max_batches: int) -> Iterator[Result]:
    """Preflight, drain, and require two clean global passes; holes resume at SQL's rewind.

    A bound stops a continuously changing dataset from creating an endless operator run.
    Stopping never erases the server checkpoint. Conflicts require investigation, not repair.
    """
    if not 1 <= batch_size <= MAX_BATCH_SIZE or max_batches < 1:
        raise BackfillError("Invalid batch size or batch limit.")
    preflight = rpc.preflight()
    if not preflight.ok:
        raise BackfillError("Preflight rejected the dataset; no batch was started.")
    yield preflight
    for _ in range(max_batches):
        batch = rpc.batch(batch_size)
        yield batch
        if not batch.drained:
            continue
        for _ in range(REQUIRED_CLEAN_PASSES):
            verification = rpc.verify()
            verification.require_consistent()
            yield verification
            verification.require_repairable()
            if verification.complete:
                return
            if verification.holes or not verification.drained:
                break
    raise BackfillError("Batch limit reached; rerun to resume from the saved checkpoint.")
