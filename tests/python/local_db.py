"""Local-only direct Postgres access for grant, role, and server-log assertions.

PostgREST only ever connects as ``authenticator`` and switches to the JWT's role, so it
cannot answer "does role X hold privilege Y?" for the migration's revoke/grant blocks, nor
observe ``RAISE LOG`` output. This helper opens a real connection to the local Supabase
Postgres (port 54322) as the ``postgres`` owner, optionally ``SET LOCAL ROLE``s to an
application role, and always rolls back.

It refuses any host other than loopback: the owner credentials it uses are the public
``supabase start`` defaults and must never be pointed at a preview or production database.
"""

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

DEFAULT_LOCAL_DB_URL = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_APPLICATION_ROLES = frozenset({"anon", "authenticated", "service_role"})


def local_db_url() -> str:
    """The direct-connection URL, validated to be loopback; raises otherwise."""
    url = os.environ.get("LOCAL_SUPABASE_DB_URL", DEFAULT_LOCAL_DB_URL)
    params = conninfo_to_dict(url)
    # A service file's settings outrank PG* environment defaults, so a selected service could
    # supply a remote hostaddr that the checks below never see.
    if params.get("service") or os.environ.get("PGSERVICE"):
        raise RuntimeError("Refusing direct Postgres access through a pg_service.conf service.")
    host = params.get("host")
    # libpq connects to hostaddr, when set, regardless of host; PGHOSTADDR is its default.
    hostaddr = params.get("hostaddr") or os.environ.get("PGHOSTADDR")
    if host not in _LOOPBACK_HOSTS or (hostaddr and hostaddr not in _LOOPBACK_HOSTS):
        # A missing host means a unix socket or a PG* environment default, neither of which
        # can be proven local from the URL, so those are refused as well.
        raise RuntimeError(
            f"Refusing direct Postgres access to non-local host {hostaddr or host!r}."
        )
    return url


@dataclass
class DirectSession:
    """A rolled-back transaction on the local database, optionally under an app role."""

    cursor: psycopg.Cursor
    notices: list[str] = field(default_factory=list)


@contextmanager
def direct_session(
    *, role: str | None = None, jwt_sub: str | None = None
) -> Iterator[DirectSession]:
    """Yield a session that is rolled back on exit.

    ``role`` is one of anon / authenticated / service_role (or ``None`` for the owner).
    ``jwt_sub`` populates ``request.jwt.claims`` so ``auth.uid()`` works under RLS.
    """
    if role is not None and role not in _APPLICATION_ROLES:
        raise ValueError(f"Unsupported role {role!r}")
    with psycopg.connect(local_db_url(), autocommit=False) as connection:
        session = DirectSession(cursor=connection.cursor())
        connection.add_notice_handler(
            lambda diagnostic: session.notices.append(diagnostic.message_primary or "")
        )
        try:
            # Surface RAISE LOG lines (normally server-log only) as client notices.
            session.cursor.execute("set client_min_messages = log")
            if jwt_sub is not None:
                session.cursor.execute(
                    "select set_config('request.jwt.claims', %s, true)",
                    (json.dumps({"sub": jwt_sub, "role": role or "authenticated"}),),
                )
            if role is not None:
                session.cursor.execute(sql.SQL("set local role {}").format(sql.Identifier(role)))
            yield session
        finally:
            connection.rollback()
