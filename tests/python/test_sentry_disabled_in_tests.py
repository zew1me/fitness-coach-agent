"""Guard: the Sentry SDK must stay inactive for the whole pytest run (issue #242).

Tests raise exceptions on purpose (mock repositories, failure paths). If the outer
shell exports ``SENTRY_DSN`` (``vercel env run``, direnv, a pulled env file), importing
``api.index`` would initialize Sentry and report those intentional failures to a real
project. ``tests/python/conftest.py`` clears the DSN before any application import.
"""

import importlib
import os

import sentry_sdk


def test_sentry_dsn_is_cleared_for_test_runs() -> None:
    assert not os.environ.get("SENTRY_DSN")


def test_importing_api_does_not_activate_sentry() -> None:
    importlib.import_module("api.index")

    assert not sentry_sdk.get_client().is_active()
